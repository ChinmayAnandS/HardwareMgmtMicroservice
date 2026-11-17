#!/usr/bin/env bash
# Progressive rollout of one Container App, with automatic rollback.
#
#   bash infra/scripts/rollout.sh <resource-group> <app> <image> <steps> <bake-seconds> <grpc|http>
#   e.g. bash infra/scripts/rollout.sh hardware-grpc hardware-grpc-app ghcr.io/o/r@sha256:... 10,50,100 180 grpc
#
# 1. Pin all traffic to the revision that serves today (the "old" one).
# 2. Create a new revision from <image>. It receives no traffic.
# 3. Smoke-test the new revision on its own label URL, before any user reaches it.
# 4. Shift traffic in <steps> (percentages, ending in 100). After each step, wait <bake-seconds> and check
#    the gates below. Any failure sends all traffic back to the old revision and exits 1.
# 5. Keep the old revision (inactive revisions beyond it are removed) so `rollback.sh` can return to it fast.
#
# Gates (environment variables): MAX_5XX (default 3 server errors from the new revision in the bake
# window), MAX_RESTARTS (default 2). gRPC application errors are HTTP 200 and do not show in the 5xx
# metric: this catches crashes, failed starts and unreachable pods, not business-logic errors.
set -Eeuo pipefail
export MSYS_NO_PATHCONV=1

rg=${1:?resource group} app=${2:?app name} image=${3:?image} steps=${4:-10,50,100} bake=${5:-180} kind=${6:-http}
max_5xx=${MAX_5XX:-3}
max_restarts=${MAX_RESTARTS:-2}
sha=${GITHUB_SHA:-local}
suffix="s${sha:0:7}-$(date -u +%d%H%M)"
label=canary
new="" old=""

log() { echo "[rollout:$app] $*"; }
summary() { [[ -n ${GITHUB_STEP_SUMMARY:-} ]] && echo "$*" >> "$GITHUB_STEP_SUMMARY" || true; }

app_id=$(az containerapp show -g "$rg" -n "$app" --query id -o tsv)
fqdn=$(az containerapp show -g "$rg" -n "$app" --query properties.configuration.ingress.fqdn -o tsv)
domain=${fqdn#*.}
label_host="$app---$label.$domain"

traffic() { az containerapp ingress traffic set -g "$rg" -n "$app" --revision-weight "$@" -o none; }

fail() {
  trap - ERR
  log "FAILED: $1"
  if [[ -n $old ]]; then
    log "rolling back: 100% of traffic to $old"
    traffic "$old=100" || log "WARNING: could not reset traffic; run rollback.sh manually"
    az containerapp revision label remove -g "$rg" -n "$app" --label "$label" -o none 2>/dev/null || true
    [[ -n $new ]] && az containerapp revision deactivate -g "$rg" -n "$app" --revision "$new" -o none 2>/dev/null || true
  fi
  summary "- **$app**: rollout FAILED ($1); traffic is back on \`${old:-unchanged}\`."
  exit 1
}

smoke() {
  case $kind in
    grpc) python scripts/smoke_grpc.py "$1:443" ;;
    http) curl --fail --silent --show-error --retry 12 --retry-delay 5 --retry-all-errors "https://$1/ready" > /dev/null ;;
    *) fail "unknown smoke kind $kind" ;;
  esac
}

# Sum a Requests/RestartCount style metric for the new revision over the last <seconds>.
metric() { # metric aggregation filter seconds
  local start end
  end=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  start=$(date -u -d "-$4 seconds" +%Y-%m-%dT%H:%M:%SZ)
  az monitor metrics list --resource "$app_id" --metric "$1" --aggregation "$2" --interval PT1M \
    --start-time "$start" --end-time "$end" --filter "$3" -o json 2> /dev/null |
    python -c "
import json, sys
data = json.load(sys.stdin)
values = [p.get('${2,,}') or 0 for s in data['value'][0]['timeseries'] for p in s['data']] if data['value'][0]['timeseries'] else []
print(int(sum(values) if '$2' == 'Total' else max(values or [0])))"
}

gates() { # seconds since the step began
  local health errors restarts
  health=$(az containerapp revision show -g "$rg" -n "$app" --revision "$new" --query properties.healthState -o tsv)
  [[ $health == Healthy ]] || fail "new revision health is '$health'"
  errors=$(metric Requests Total "revisionName eq '$new' and statusCodeCategory eq '5xx'" "$1")
  restarts=$(metric RestartCount Maximum "revisionName eq '$new'" "$1")
  log "gates: health=$health 5xx=$errors (max $max_5xx) restarts=$restarts (max $max_restarts)"
  (( errors <= max_5xx )) || fail "$errors server errors from the new revision"
  (( restarts <= max_restarts )) || fail "new revision restarted $restarts times"
  smoke "$fqdn"
}

# --- 1. who serves traffic today
old=$(az containerapp ingress traffic show -g "$rg" -n "$app" --query "[?weight==\`100\`] | [0].revisionName" -o tsv 2> /dev/null || true)
if [[ -z $old || $old == None ]]; then
  old=$(az containerapp show -g "$rg" -n "$app" --query properties.latestReadyRevisionName -o tsv)
fi
log "serving revision: $old"
traffic "$old=100"

# --- 2. new revision (no traffic: it is not in the weights)
trap 'fail "command failed at line $LINENO"' ERR
new="$app--$suffix"
log "creating $new from $image"
az containerapp update -g "$rg" -n "$app" --image "$image" --revision-suffix "$suffix" -o none

for _ in $(seq 1 60); do
  state=$(az containerapp revision show -g "$rg" -n "$app" --revision "$new" --query properties.provisioningState -o tsv)
  [[ $state == Provisioned ]] && break
  [[ $state == Failed ]] && fail "revision provisioning failed"
  sleep 10
done
[[ $state == Provisioned ]] || fail "revision did not finish provisioning in 10 minutes"

# --- 3. test it privately on its label URL
az containerapp revision label remove -g "$rg" -n "$app" --label "$label" -o none 2> /dev/null || true
az containerapp revision label add -g "$rg" -n "$app" --label "$label" --revision "$new" -o none
log "smoke test on https://$label_host"
smoke "$label_host"

# --- 4. shift traffic gradually
IFS=', ' read -ra pcts <<< "$steps"
[[ ${pcts[-1]} == 100 ]] || fail "steps must end in 100 (got: $steps)"
for pct in "${pcts[@]}"; do
  if (( pct < 100 )); then traffic "$new=$pct" "$old=$((100 - pct))"; else traffic "$new=100"; fi
  log "traffic: $pct% on $new; waiting ${bake}s"
  sleep "$bake"
  gates "$((bake + 60))"
done

# --- 5. tidy up: keep old (instant rollback) and new, remove the rest
az containerapp revision label remove -g "$rg" -n "$app" --label "$label" -o none 2> /dev/null || true
for stale in $(az containerapp revision list -g "$rg" -n "$app" --query "[?properties.active].name" -o tsv); do
  [[ $stale == "$new" || $stale == "$old" ]] || az containerapp revision deactivate -g "$rg" -n "$app" --revision "$stale" -o none || true
done
trap - ERR
log "done: $new serves 100%; $old kept for rollback"
summary "- **$app**: \`$new\` now serves 100% (steps $steps, ${bake}s bake); \`$old\` kept for rollback."
