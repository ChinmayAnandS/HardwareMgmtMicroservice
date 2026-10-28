#!/usr/bin/env bash
set -euo pipefail
stage=${1:?Usage: bash infra/monitoring.sh dev-or-prod operations-email}
email=${2:?Operations email required}
[[ $stage == dev || $stage == prod ]] || exit 2
rg="hardware-$stage"
az monitor action-group create -g "$rg" -n hardware-ops --short-name hwops --action email operations "$email" -o none
action=$(az monitor action-group show -g "$rg" -n hardware-ops --query id -o tsv)
for app in hardware-api hardware-ui; do
  resource=$(az containerapp show -g "$rg" -n "$app" --query id -o tsv)
  az monitor metrics alert create -g "$rg" -n "$app-server-errors" --scopes "$resource" --condition 'total Requests > 5 where statusCodeCategory includes 5xx' --window-size 5m --evaluation-frequency 1m --action "$action" --description 'Hardware service server errors in five minutes' -o none
done
