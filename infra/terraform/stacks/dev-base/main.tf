# Long-lived part of the dev environment: Log Analytics and the Container Apps environment that
# every ephemeral preview (stacks/dev-preview) deploys into. The resource group is created by
# bootstrap.sh so the GitHub identity can be scoped to it.
terraform {
  required_version = ">= 1.6"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
  backend "azurerm" {}
}

provider "azurerm" {
  features {}
  subscription_id                 = var.subscription_id
  resource_provider_registrations = "none"
}

variable "subscription_id" {
  type = string
}

variable "resource_group_name" {
  type    = string
  default = "hardware-dev-rg"
}

data "azurerm_resource_group" "dev" {
  name = var.resource_group_name
}

resource "azurerm_log_analytics_workspace" "dev" {
  name                = "hardware-dev-logs"
  location            = data.azurerm_resource_group.dev.location
  resource_group_name = data.azurerm_resource_group.dev.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  daily_quota_gb      = 1
}

# A workload-profiles environment (not "express"): HTTP/2 ingress, which gRPC needs, only works here.
resource "azurerm_container_app_environment" "dev" {
  name                       = "hardware-dev-env"
  location                   = data.azurerm_resource_group.dev.location
  resource_group_name        = data.azurerm_resource_group.dev.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.dev.id

  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }
}

output "environment_name" {
  value = azurerm_container_app_environment.dev.name
}

output "default_domain" {
  value = azurerm_container_app_environment.dev.default_domain
}
