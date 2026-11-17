#!/usr/bin/env bash
# Send all traffic back to an earlier revision, fast.
#
#   bash infra/scripts/rollback.sh <resource-group> <app> [revision]
#
# Without [revision] it picks the newest active revision that is not serving 100% of traffic now,
# which after a normal rollout is the one that was serving before.
set -Eeuo pipefail
export MSYS_NO_PATHCONV=1

rg=${1:?resource group} app=${2:?app name} target=${3:-}

current=$(az containerapp ingress traffic show -g "$rg" -n "$app" --query "[?weight==\`100\`] | [0].revisionName" -o tsv 2> /dev/null || true)
[[ $current == None ]] && current=""
if [[ -z $target ]]; then
  target=$(az containerapp revision list -g "$rg" -n "$app" --query "sort_by([?properties.active], &properties.createdTime)[].name" -o tsv |
    grep -vxF "${current:-__none__}" | tail -n 1 || true)
fi
[[ -n $target ]] || { echo "No earlier active revision to roll back to." >&2; exit 1; }

echo "[rollback:$app] ${current:-split traffic} -> $target"
az containerapp revision activate -g "$rg" -n "$app" --revision "$target" -o none 2> /dev/null || true
az containerapp ingress traffic set -g "$rg" -n "$app" --revision-weight "$target=100" -o none
az containerapp revision label remove -g "$rg" -n "$app" --label canary -o none 2> /dev/null || true
echo "[rollback:$app] done: 100% on $target"
[[ -n ${GITHUB_STEP_SUMMARY:-} ]] && echo "- **$app**: rolled back to \`$target\` (was \`${current:-split}\`)." >> "$GITHUB_STEP_SUMMARY" || true
