#!/usr/bin/env bash
# One-time Azure setup for the Terraform pipeline. Run it yourself after `az login`:
#   bash infra/terraform/bootstrap.sh <azure-region> <github-owner/repository>
# Creates the remote-state storage account, the dev resource group, and a GitHub OIDC identity
# (no stored secrets) scoped to the dev resource group and the state account. Safe to re-run.
set -euo pipefail
# Git Bash on Windows rewrites arguments that start with '/' (like --scope /subscriptions/...) into file paths.
export MSYS_NO_PATHCONV=1
region=${1:?Usage: bash infra/terraform/bootstrap.sh azure-region owner/repository}
repo=${2:?GitHub owner/repository required}
subscription=$(az account show --query id -o tsv 2>/dev/null || true)
if [[ -z $subscription ]]; then
  echo "No active Azure subscription. Run 'az login --tenant <tenant>' and 'az account set --subscription <id>' first;" >&2
  echo "'az account list -o table' shows what you can use." >&2
  exit 1
fi
tenant=$(az account show --query tenantId -o tsv)
echo "Using subscription: $(az account show --query name -o tsv) ($subscription)"
read -r -p "Continue? [y/N] " answer
[[ $answer == [yY]* ]] || exit 1
for provider in Microsoft.App Microsoft.OperationalInsights Microsoft.Insights Microsoft.Storage; do
  az provider register --namespace "$provider" --wait -o none
done

suffix=$(printf '%s' "$subscription" | sha256sum | cut -c1-8)
state_rg=hardware-tfstate
state_sa="hwtf${suffix}"
dev_rg=hardware-dev-rg
az group create -n "$state_rg" -l "$region" -o none
az storage account create -g "$state_rg" -n "$state_sa" -l "$region" --sku Standard_LRS \
  --allow-blob-public-access false --min-tls-version TLS1_2 -o none
state_id=$(az storage account show -g "$state_rg" -n "$state_sa" --query id -o tsv)
az group create -n "$dev_rg" -l "$region" -o none

me=$(az ad signed-in-user show --query id -o tsv)
az role assignment create --assignee-object-id "$me" --assignee-principal-type User \
  --role "Storage Blob Data Contributor" --scope "$state_id" -o none
# Role assignments can take a minute to apply before the container can be created.
for _ in 1 2 3 4 5 6; do
  az storage container create --account-name "$state_sa" -n tfstate --auth-mode login -o none 2>/dev/null && break
  sleep 15
done

az identity create -g "$state_rg" -n github-deploy-dev -o none
client=$(az identity show -g "$state_rg" -n github-deploy-dev --query clientId -o tsv)
principal=$(az identity show -g "$state_rg" -n github-deploy-dev --query principalId -o tsv)
az identity federated-credential create -g "$state_rg" --identity-name github-deploy-dev -n github-dev \
  --issuer https://token.actions.githubusercontent.com --subject "repo:$repo:environment:dev" \
  --audiences api://AzureADTokenExchange -o none 2>/dev/null || true
az role assignment create --assignee-object-id "$principal" --assignee-principal-type ServicePrincipal \
  --role Contributor --scope "$(az group show -n "$dev_rg" --query id -o tsv)" -o none
az role assignment create --assignee-object-id "$principal" --assignee-principal-type ServicePrincipal \
  --role "Storage Blob Data Contributor" --scope "$state_id" -o none

echo
echo "Add these as variables (not secrets) on the GitHub Environment named 'dev':"
printf 'AZURE_CLIENT_ID=%s\nAZURE_TENANT_ID=%s\nAZURE_SUBSCRIPTION_ID=%s\n' "$client" "$tenant" "$subscription"
printf 'TF_STATE_RESOURCE_GROUP=%s\nTF_STATE_STORAGE_ACCOUNT=%s\nTF_STATE_CONTAINER=tfstate\n' "$state_rg" "$state_sa"
