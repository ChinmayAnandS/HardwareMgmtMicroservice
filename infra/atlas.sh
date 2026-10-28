#!/usr/bin/env bash
set -euo pipefail
stage=${1:?Usage: bash infra/atlas.sh dev-or-prod atlas-project-id atlas-azure-region}
project=${2:?Atlas project ID required}
region=${3:?Atlas Azure region code required, e.g. US_CENTRAL}
[[ $stage == dev || $stage == prod ]] || exit 2
rg="hardware-$stage"
cluster="hardware-$stage"
atlas auth login
atlas clusters create "$cluster" --projectId "$project" --provider AZURE --region "$region" --members 3 --tier M10 --mdbVersion 8.0 --diskSizeGB 10 --backup
atlas clusters watch "$cluster" --projectId "$project"
egress=$(az network public-ip show -g "$rg" -n hardware-egress --query ipAddress -o tsv)
atlas accessLists create "$egress/32" --type cidrBlock --projectId "$project" --comment 'Hardware Container Apps NAT egress'
privileges=()
for collection in hardware allocations checkouts; do
  for action in FIND INSERT UPDATE; do privileges+=("$action@hardware.$collection"); done
done
for collection in audit requests; do
  for action in FIND INSERT; do privileges+=("$action@hardware.$collection"); done
done
for collection in sessions limits; do
  for action in FIND INSERT UPDATE REMOVE; do privileges+=("$action@hardware.$collection"); done
done
joined=$(IFS=,; printf '%s' "${privileges[*]}")
atlas customDbRoles create hardwareApp --projectId "$project" --privilege "$joined"
# CLI prompts for the password. Generate it in a password manager; never paste it into a command.
atlas dbusers create --projectId "$project" --username hardware_app --role hardwareApp --scope "$cluster"
printf 'Create a temporary provisioning user with readWrite@hardware,dbAdmin@hardware using Atlas CLI.\n'
printf 'Apply scripts/schema.js with mongosh from an allow-listed workstation, then remove that user and its IP access entry.\n'
printf 'Store the application SRV URI with scripts/store-mongo-uri.py %s.\n' "$stage"
