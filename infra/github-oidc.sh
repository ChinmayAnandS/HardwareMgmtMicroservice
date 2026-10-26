#!/usr/bin/env bash
set -euo pipefail
stage=${1:?Usage: bash infra/github-oidc.sh dev-or-prod owner/repository}
repo=${2:?GitHub owner/repository required}
[[ $stage == dev || $stage == prod ]] || exit 2
rg="hardware-$stage"
az identity create -g "$rg" -n github-deploy -o none
client=$(az identity show -g "$rg" -n github-deploy --query clientId -o tsv)
principal=$(az identity show -g "$rg" -n github-deploy --query principalId -o tsv)
az identity federated-credential create -g "$rg" --identity-name github-deploy -n "github-$stage" --issuer https://token.actions.githubusercontent.com --subject "repo:$repo:environment:$stage" --audiences api://AzureADTokenExchange -o none
az role assignment create --assignee-object-id "$principal" --assignee-principal-type ServicePrincipal --role Contributor --scope "$(az group show -n "$rg" --query id -o tsv)" -o none
acr=$(az acr list -g "$rg" --query '[0].name' -o tsv)
az role assignment create --assignee-object-id "$principal" --assignee-principal-type ServicePrincipal --role AcrPush --scope "$(az acr show -n "$acr" --query id -o tsv)" -o none
printf 'AZURE_CLIENT_ID=%s\n' "$client"
az account show --query '{AZURE_TENANT_ID:tenantId,AZURE_SUBSCRIPTION_ID:id}' -o json
printf 'AZURE_RESOURCE_GROUP=%s\nAZURE_ACR=%s\n' "$rg" "$acr"
