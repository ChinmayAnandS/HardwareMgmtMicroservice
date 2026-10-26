#!/usr/bin/env bash
set -euo pipefail
stage=${1:?Usage: bash infra/bootstrap.sh dev-or-prod azure-region}
region=${2:?Azure region required}
[[ $stage == dev || $stage == prod ]] || exit 2
az account show -o none
az extension add --name containerapp --upgrade --only-show-errors
for provider in Microsoft.App Microsoft.OperationalInsights Microsoft.Network Microsoft.ContainerRegistry Microsoft.KeyVault Microsoft.Insights; do
  az provider register --namespace "$provider" --wait -o none
done
subscription=$(az account show --query id -o tsv)
suffix=$(printf '%s' "$subscription-$stage" | sha256sum | cut -c1-10)
rg="hardware-$stage"; acr="hw${suffix}"; kv="hw-${suffix}"; environment="hardware-$stage"
az group create -n "$rg" -l "$region" -o none
az acr create -g "$rg" -n "$acr" --sku Basic --admin-enabled false -o none
az acr config authentication-as-arm update -r "$acr" --status enabled -o none
az identity create -g "$rg" -n runtime -o none
identity=$(az identity show -g "$rg" -n runtime --query id -o tsv)
principal=$(az identity show -g "$rg" -n runtime --query principalId -o tsv)
az role assignment create --assignee-object-id "$principal" --assignee-principal-type ServicePrincipal --role AcrPull --scope "$(az acr show -n "$acr" --query id -o tsv)" -o none
az keyvault create -g "$rg" -n "$kv" --enable-rbac-authorization true --retention-days 7 -o none
az role assignment create --assignee-object-id "$principal" --assignee-principal-type ServicePrincipal --role 'Key Vault Secrets User' --scope "$(az keyvault show -n "$kv" --query id -o tsv)" -o none
operator=$(az ad signed-in-user show --query id -o tsv)
az role assignment create --assignee-object-id "$operator" --assignee-principal-type User --role 'Key Vault Secrets Officer' --scope "$(az keyvault show -n "$kv" --query id -o tsv)" -o none
az monitor log-analytics workspace create -g "$rg" -n "hardware-$stage" --retention-time 30 --quota 1 -o none
workspace=$(az monitor log-analytics workspace show -g "$rg" -n "hardware-$stage" --query customerId -o tsv)
workspace_key=$(az monitor log-analytics workspace get-shared-keys -g "$rg" -n "hardware-$stage" --query primarySharedKey -o tsv)
az network vnet create -g "$rg" -n hardware-vnet --address-prefixes 10.77.0.0/16 --subnet-name apps --subnet-prefixes 10.77.0.0/23 -o none
az network vnet subnet update -g "$rg" --vnet-name hardware-vnet -n apps --delegations Microsoft.App/environments -o none
az network public-ip create -g "$rg" -n hardware-egress --sku Standard --allocation-method Static -o none
az network nat gateway create -g "$rg" -n hardware-nat --public-ip-addresses hardware-egress --idle-timeout 10 -o none
az network vnet subnet update -g "$rg" --vnet-name hardware-vnet -n apps --nat-gateway hardware-nat -o none
subnet=$(az network vnet subnet show -g "$rg" --vnet-name hardware-vnet -n apps --query id -o tsv)
az containerapp env create -g "$rg" -n "$environment" -l "$region" --enable-workload-profiles --infrastructure-subnet-resource-id "$subnet" --logs-workspace-id "$workspace" --logs-workspace-key "$workspace_key" -o none
unset workspace_key
workspace_resource=$(az monitor log-analytics workspace show -g "$rg" -n "hardware-$stage" --query id -o tsv)
az extension add --name application-insights --upgrade --only-show-errors
az monitor app-insights component create -g "$rg" --app hardware --location "$region" --workspace "$workspace_resource" -o none
mkdir -p .secrets
chmod 700 .secrets
az containerapp env show -g "$rg" -n "$environment" -o json > ".secrets/environment-$stage.json"
az network public-ip show -g "$rg" -n hardware-egress --query ipAddress -o tsv
printf 'Resource group: %s\nACR: %s\nKey Vault: %s\nRuntime identity: %s\n' "$rg" "$acr" "$kv" "$identity"
printf 'Add ONLY the printed egress IP /32 to Atlas. Next follow docs/AZURE.md.\n'
