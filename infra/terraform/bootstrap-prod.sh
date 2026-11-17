#!/usr/bin/env bash
# One-time Azure setup for the production pipeline (the dev one is bootstrap.sh). Run it yourself
# after `az login` and after bootstrap.sh:
#   bash infra/terraform/bootstrap-prod.sh <github-owner/repository> [prod-resource-group]
# Creates a separate GitHub identity for the `prod` Environment, with Contributor on the prod
# resource group only, and a separate state container that the dev identity cannot reach.
# Safe to re-run.
set -euo pipefail
# Git Bash on Windows rewrites arguments that start with '/' (like --scope /subscriptions/...) into file paths.
export MSYS_NO_PATHCONV=1
repo=${1:?Usage: bash infra/terraform/bootstrap-prod.sh owner/repository [prod-resource-group]}
prod_rg=${2:-hardware-grpc}
subscription=$(az account show --query id -o tsv 2> /dev/null || true)
if [[ -z $subscription ]]; then
  echo "No active Azure subscription. Run 'az login --tenant <tenant>' first." >&2
  exit 1
fi
tenant=$(az account show --query tenantId -o tsv)
echo "Using subscription: $(az account show --query name -o tsv) ($subscription); prod resource group: $prod_rg"
read -r -p "Continue? [y/N] " answer
[[ $answer == [yY]* ]] || exit 1

state_rg=hardware-tfstate
state_sa=$(az storage account list -g "$state_rg" --query "[0].name" -o tsv)
[[ -n $state_sa ]] || { echo "Run bootstrap.sh first: no state storage account found in $state_rg." >&2; exit 1; }
state_id=$(az storage account show -g "$state_rg" -n "$state_sa" --query id -o tsv)
container=tfstate-prod
az storage container create --account-name "$state_sa" -n "$container" --auth-mode login -o none

az identity create -g "$state_rg" -n github-deploy-prod -o none
client=$(az identity show -g "$state_rg" -n github-deploy-prod --query clientId -o tsv)
principal=$(az identity show -g "$state_rg" -n github-deploy-prod --query principalId -o tsv)
az identity federated-credential create -g "$state_rg" --identity-name github-deploy-prod -n github-prod \
  --issuer https://token.actions.githubusercontent.com --subject "repo:$repo:environment:prod" \
  --audiences api://AzureADTokenExchange -o none 2> /dev/null || true
ids=$(curl -fsS "https://api.github.com/repos/$repo" | python -c "import json,sys; d=json.load(sys.stdin); print(d['owner']['id'], d['id'])" 2> /dev/null || true)
if [[ -n $ids ]]; then
  owner=${repo%%/*}; name=${repo#*/}; owner_id=${ids% *}; repo_id=${ids#* }
  az identity federated-credential create -g "$state_rg" --identity-name github-deploy-prod -n github-prod-immutable \
    --issuer https://token.actions.githubusercontent.com \
    --subject "repo:${owner}@${owner_id}/${name}@${repo_id}:environment:prod" \
    --audiences api://AzureADTokenExchange -o none 2> /dev/null || true
else
  echo "Warning: could not look up the GitHub IDs; add the ID-based subject if the login says 'No matching federated identity record'." >&2
fi

# Contributor on the prod resource group: enough to manage the apps, alerts and logs there.
az role assignment create --assignee-object-id "$principal" --assignee-principal-type ServicePrincipal \
  --role Contributor --scope "$(az group show -n "$prod_rg" --query id -o tsv)" -o none
# State access for this container only.
az role assignment create --assignee-object-id "$principal" --assignee-principal-type ServicePrincipal \
  --role "Storage Blob Data Contributor" --scope "$state_id/blobServices/default/containers/$container" -o none

echo
echo "Add these as variables (not secrets) on the GitHub Environment named 'prod':"
printf 'AZURE_CLIENT_ID=%s\nAZURE_TENANT_ID=%s\nAZURE_SUBSCRIPTION_ID=%s\n' "$client" "$tenant" "$subscription"
printf 'TF_STATE_RESOURCE_GROUP=%s\nTF_STATE_STORAGE_ACCOUNT=%s\nTF_STATE_CONTAINER=%s\n' "$state_rg" "$state_sa" "$container"
printf 'OPS_EMAIL=<the address that should receive alerts>\n'
