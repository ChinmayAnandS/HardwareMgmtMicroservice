# One ephemeral preview: the API and website apps for a single pipeline run, deployed into the
# shared dev environment and destroyed when the run ends. State is keyed per run (see
# .github/workflows/deploy-dev.yml), so concurrent runs never touch each other.
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

variable "run_id" {
  type        = string
  description = "Short unique id for this preview (lowercase letters and digits)."
  validation {
    condition     = can(regex("^[a-z0-9]{3,12}$", var.run_id))
    error_message = "run_id must be 3 to 12 lowercase letters or digits (it becomes part of the app names)."
  }
}

variable "resource_group_name" {
  type    = string
  default = "hardware-dev-rg"
}

# This subscription allows exactly one standard (HTTP/2-capable) Container Apps environment, and prod
# already uses it, so previews attach to it as separate apps in the dev resource group. They get their
# own hostnames; prod's apps and URLs are untouched. See bootstrap.sh for the narrow role this needs.
variable "environment_name" {
  type    = string
  default = "managed-westus-1uv3hoo"
}

variable "environment_resource_group_name" {
  type    = string
  default = "hardware-grpc"
}

variable "api_image" {
  type = string
}

variable "web_image" {
  type = string
}

variable "mongo_uri" {
  type      = string
  sensitive = true
}

variable "mongo_database" {
  type        = string
  description = "Database for this preview, dropped when it is torn down."
}

variable "hardware_admin_code" {
  type      = string
  sensitive = true
}

variable "dev_login_code" {
  type      = string
  sensitive = true
}

data "azurerm_container_app_environment" "dev" {
  name                = var.environment_name
  resource_group_name = var.environment_resource_group_name
}

module "apps" {
  source = "../../modules/hardware_apps"

  name_suffix                = var.run_id
  resource_group_name        = var.resource_group_name
  environment_id             = data.azurerm_container_app_environment.dev.id
  environment_default_domain = data.azurerm_container_app_environment.dev.default_domain
  api_image                  = var.api_image
  web_image                  = var.web_image

  # "test" keeps the browser dev sign-in the end-to-end tests use; production mode forbids it.
  api_env = {
    ENVIRONMENT    = "test"
    MONGO_DATABASE = var.mongo_database
    GRPC_PORT      = "50051"
  }
  api_secret_env = {
    MONGO_URI                = var.mongo_uri
    TEAM_CODE_HARDWARE_ADMIN = var.hardware_admin_code
    DEV_LOGIN_CODE           = var.dev_login_code
  }
  tags = {
    ephemeral = "true"
    run       = var.run_id
  }
}

output "api_fqdn" {
  value = module.apps.api_fqdn
}

output "web_url" {
  value = "https://${module.apps.web_fqdn}"
}
