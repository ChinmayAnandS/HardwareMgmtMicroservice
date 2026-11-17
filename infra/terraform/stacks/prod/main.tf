# Production: adopts the two live Container Apps (their URLs must not change, so they are imported,
# never recreated) and adds the logging and alerting around them.
#
# What Terraform owns here: ingress, probes, scale, revision mode, log storage and alerts.
# What it deliberately leaves alone (lifecycle.ignore_changes):
#   - the container image and the ingress traffic weights: the deploy pipeline changes these on every
#     release (canary), so Terraform must not fight it;
#   - environment variables and secrets: prod keeps the Mongo URI and team codes as plain env values
#     today. They stay managed in Azure until they are moved to GitHub secrets, so no credential is
#     copied into this configuration or its state by this stack.
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
  default = "hardware-grpc"
}

variable "environment_name" {
  type    = string
  default = "managed-westus-1uv3hoo"
}

variable "api_app_name" {
  type    = string
  default = "hardware-grpc-app"
}

variable "web_app_name" {
  type    = string
  default = "hardware-grpc-webapp"
}

# Used only to describe the apps when they are created; ignored for the live ones (see above).
variable "api_image" {
  type    = string
  default = "ghcr.io/chinmayanands/hardwaremgmtmicroservice:987bf69c832f68c83df50c8e82a3e29dc54f8063"
}

variable "web_image" {
  type    = string
  default = "ghcr.io/chinmayanands/hardwaremgmtmicroservice-web:e67fbf72f531bb166967ffac6df0802c6f0f9cb7"
}

variable "min_replicas" {
  type        = number
  default     = 0
  description = "0 saves money but the first call after idle waits for a cold start. Use 1 to keep the API warm."
}

variable "ops_email" {
  type        = string
  description = "Where alerts are sent."
}

variable "spike_requests_per_5m" {
  type        = number
  default     = 3000
  description = "Alert when the API receives more than this many requests in five minutes."
}

variable "spike_rx_bytes_per_5m" {
  type        = number
  default     = 50000000
  description = "Alert when the API receives more than this many bytes in five minutes."
}

data "azurerm_resource_group" "prod" {
  name = var.resource_group_name
}

data "azurerm_container_app_environment" "prod" {
  name                = var.environment_name
  resource_group_name = var.resource_group_name
}

# ---------------------------------------------------------------------------------------------
# The live apps (adopted by the import blocks, which Terraform runs once and then ignores).
# ---------------------------------------------------------------------------------------------
import {
  to = azurerm_container_app.api
  id = "/subscriptions/${var.subscription_id}/resourceGroups/${var.resource_group_name}/providers/Microsoft.App/containerApps/${var.api_app_name}"
}

import {
  to = azurerm_container_app.web
  id = "/subscriptions/${var.subscription_id}/resourceGroups/${var.resource_group_name}/providers/Microsoft.App/containerApps/${var.web_app_name}"
}

resource "azurerm_container_app" "api" {
  name                         = var.api_app_name
  resource_group_name          = var.resource_group_name
  container_app_environment_id = data.azurerm_container_app_environment.prod.id
  # Multiple lets a new revision run beside the old one so traffic can be shifted gradually.
  revision_mode         = "Multiple"
  workload_profile_name = "Consumption"
  # Matches the live app (the provider would otherwise plan to clear it).
  max_inactive_revisions = 100

  ingress {
    external_enabled        = true
    target_port             = 50051
    transport               = "http2"
    client_certificate_mode = "ignore"
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = var.min_replicas
    max_replicas = 1

    container {
      name   = "hardware-grpc-app"
      image  = var.api_image
      cpu    = 0.5
      memory = "1Gi"

      startup_probe {
        transport               = "TCP"
        port                    = 50051
        initial_delay           = 1
        interval_seconds        = 1
        timeout                 = 3
        failure_count_threshold = 240
      }
      readiness_probe {
        transport               = "TCP"
        port                    = 50051
        interval_seconds        = 5
        timeout                 = 5
        failure_count_threshold = 48
        success_count_threshold = 1
      }
      liveness_probe {
        initial_delay           = 0
        transport               = "TCP"
        port                    = 50051
        interval_seconds        = 10
        timeout                 = 5
        failure_count_threshold = 3
      }
    }
  }

  lifecycle {
    ignore_changes = [
      ingress[0].traffic_weight,
      template[0].container[0].image,
      template[0].container[0].env,
      template[0].revision_suffix,
      secret,
    ]
  }
}

resource "azurerm_container_app" "web" {
  name                         = var.web_app_name
  resource_group_name          = var.resource_group_name
  container_app_environment_id = data.azurerm_container_app_environment.prod.id
  revision_mode                = "Multiple"
  workload_profile_name        = "Consumption"
  max_inactive_revisions       = 100

  ingress {
    external_enabled = true
    target_port      = 8080
    transport        = "auto"
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = var.min_replicas
    max_replicas = 1

    container {
      name   = "hardware-grpc-webapp"
      image  = var.web_image
      cpu    = 0.5
      memory = "1Gi"

      startup_probe {
        transport               = "TCP"
        port                    = 8080
        initial_delay           = 1
        interval_seconds        = 1
        timeout                 = 3
        failure_count_threshold = 240
      }
      readiness_probe {
        transport               = "TCP"
        port                    = 8080
        interval_seconds        = 5
        timeout                 = 5
        failure_count_threshold = 48
        success_count_threshold = 1
      }
      liveness_probe {
        initial_delay           = 0
        transport               = "TCP"
        port                    = 8080
        interval_seconds        = 10
        timeout                 = 5
        failure_count_threshold = 3
      }
    }
  }

  lifecycle {
    ignore_changes = [
      ingress[0].traffic_weight,
      template[0].container[0].image,
      template[0].container[0].env,
      template[0].revision_suffix,
      secret,
    ]
  }
}

# ---------------------------------------------------------------------------------------------
# Logs: the environment sends logs to "azure-monitor" but has no diagnostic setting, so nothing is
# stored. This keeps the console and system logs (including the rpc= and [access] lines) for 30 days.
# ---------------------------------------------------------------------------------------------
resource "azurerm_log_analytics_workspace" "prod" {
  name                = "hardware-prod-logs"
  location            = data.azurerm_resource_group.prod.location
  resource_group_name = data.azurerm_resource_group.prod.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  daily_quota_gb      = 1
}

resource "azurerm_monitor_diagnostic_setting" "environment_logs" {
  name                       = "to-log-analytics"
  target_resource_id         = data.azurerm_container_app_environment.prod.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.prod.id

  enabled_log {
    category = "ContainerAppConsoleLogs"
  }
  enabled_log {
    category = "ContainerAppSystemLogs"
  }
}

# ---------------------------------------------------------------------------------------------
# Alerts (email). The rollout in deploy-prod.yml has its own gates; these watch the steady state.
# ---------------------------------------------------------------------------------------------
resource "azurerm_monitor_action_group" "ops" {
  name                = "hardware-prod-ops"
  resource_group_name = data.azurerm_resource_group.prod.name
  short_name          = "hwops"

  email_receiver {
    name                    = "operations"
    email_address           = var.ops_email
    use_common_alert_schema = true
  }
}

resource "azurerm_monitor_metric_alert" "api_server_errors" {
  name                = "hardware-api-5xx"
  resource_group_name = data.azurerm_resource_group.prod.name
  scopes              = [azurerm_container_app.api.id]
  description         = "More than 5 server errors in five minutes (the gateway could not reach a healthy API)."
  severity            = 2
  frequency           = "PT1M"
  window_size         = "PT5M"

  criteria {
    metric_namespace = "microsoft.app/containerapps"
    metric_name      = "Requests"
    aggregation      = "Total"
    operator         = "GreaterThan"
    threshold        = 5
    dimension {
      name     = "statusCodeCategory"
      operator = "Include"
      values   = ["5xx"]
    }
  }
  action {
    action_group_id = azurerm_monitor_action_group.ops.id
  }
}

resource "azurerm_monitor_metric_alert" "api_request_spike" {
  name                = "hardware-api-request-spike"
  resource_group_name = data.azurerm_resource_group.prod.name
  scopes              = [azurerm_container_app.api.id]
  description         = "An unusually high number of requests in five minutes (possible abuse or a runaway client)."
  severity            = 2
  frequency           = "PT1M"
  window_size         = "PT5M"

  criteria {
    metric_namespace = "microsoft.app/containerapps"
    metric_name      = "Requests"
    aggregation      = "Total"
    operator         = "GreaterThan"
    threshold        = var.spike_requests_per_5m
  }
  action {
    action_group_id = azurerm_monitor_action_group.ops.id
  }
}

resource "azurerm_monitor_metric_alert" "api_traffic_anomaly" {
  name                = "hardware-api-traffic-anomaly"
  resource_group_name = data.azurerm_resource_group.prod.name
  scopes              = [azurerm_container_app.api.id]
  description         = "Request volume far above this API's learned normal (needs a few days of history to learn)."
  severity            = 3
  frequency           = "PT5M"
  window_size         = "PT15M"

  dynamic_criteria {
    metric_namespace         = "microsoft.app/containerapps"
    metric_name              = "Requests"
    aggregation              = "Total"
    operator                 = "GreaterThan"
    alert_sensitivity        = "Medium"
    evaluation_total_count   = 4
    evaluation_failure_count = 3
  }
  action {
    action_group_id = azurerm_monitor_action_group.ops.id
  }
}

resource "azurerm_monitor_metric_alert" "api_bandwidth_spike" {
  name                = "hardware-api-inbound-bandwidth-spike"
  resource_group_name = data.azurerm_resource_group.prod.name
  scopes              = [azurerm_container_app.api.id]
  description         = "Unusually many inbound bytes in five minutes."
  severity            = 2
  frequency           = "PT1M"
  window_size         = "PT5M"

  criteria {
    metric_namespace = "microsoft.app/containerapps"
    metric_name      = "RxBytes"
    aggregation      = "Total"
    operator         = "GreaterThan"
    threshold        = var.spike_rx_bytes_per_5m
  }
  action {
    action_group_id = azurerm_monitor_action_group.ops.id
  }
}

resource "azurerm_monitor_metric_alert" "api_restarts" {
  name                = "hardware-api-restarts"
  resource_group_name = data.azurerm_resource_group.prod.name
  scopes              = [azurerm_container_app.api.id]
  description         = "The API container restarted more than 3 times in 15 minutes."
  severity            = 1
  frequency           = "PT5M"
  window_size         = "PT15M"

  criteria {
    metric_namespace = "microsoft.app/containerapps"
    metric_name      = "RestartCount"
    aggregation      = "Maximum"
    operator         = "GreaterThan"
    threshold        = 3
  }
  action {
    action_group_id = azurerm_monitor_action_group.ops.id
  }
}

resource "azurerm_monitor_metric_alert" "web_server_errors" {
  name                = "hardware-web-5xx"
  resource_group_name = data.azurerm_resource_group.prod.name
  scopes              = [azurerm_container_app.web.id]
  description         = "More than 5 server errors from the website in five minutes."
  severity            = 2
  frequency           = "PT1M"
  window_size         = "PT5M"

  criteria {
    metric_namespace = "microsoft.app/containerapps"
    metric_name      = "Requests"
    aggregation      = "Total"
    operator         = "GreaterThan"
    threshold        = 5
    dimension {
      name     = "statusCodeCategory"
      operator = "Include"
      values   = ["5xx"]
    }
  }
  action {
    action_group_id = azurerm_monitor_action_group.ops.id
  }
}

output "api_fqdn" {
  value = azurerm_container_app.api.ingress[0].fqdn
}

output "web_fqdn" {
  value = azurerm_container_app.web.ingress[0].fqdn
}
