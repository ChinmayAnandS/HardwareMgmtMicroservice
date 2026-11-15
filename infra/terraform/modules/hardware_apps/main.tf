# The two Container Apps: the gRPC API (HTTP/2 ingress, port 50051) and the website
# (Nginx + Envoy gRPC-Web, HTTP ingress, port 8080) that talks to it over TLS.
locals {
  api_name = "hardware-api-${var.name_suffix}"
  web_name = "hardware-web-${var.name_suffix}"
  api_fqdn = "${local.api_name}.${var.environment_default_domain}"
  web_fqdn = "${local.web_name}.${var.environment_default_domain}"
}

resource "azurerm_container_app" "api" {
  name                         = local.api_name
  resource_group_name          = var.resource_group_name
  container_app_environment_id = var.environment_id
  revision_mode                = var.revision_mode
  workload_profile_name        = "Consumption"
  tags                         = var.tags

  dynamic "secret" {
    for_each = nonsensitive(toset(keys(var.api_secret_env)))
    content {
      name  = lower(replace(secret.value, "_", "-"))
      value = var.api_secret_env[secret.value]
    }
  }

  ingress {
    external_enabled = true
    target_port      = 50051
    transport        = "http2"
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = var.min_replicas
    max_replicas = var.max_replicas

    container {
      name   = "api"
      image  = var.api_image
      cpu    = 0.5
      memory = "1Gi"

      dynamic "env" {
        for_each = var.api_env
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = nonsensitive(toset(keys(var.api_secret_env)))
        content {
          name        = env.value
          secret_name = lower(replace(env.value, "_", "-"))
        }
      }

      # The provider has no gRPC probe type; a TCP check on the gRPC port is the closest match.
      startup_probe {
        transport               = "TCP"
        port                    = 50051
        interval_seconds        = 5
        failure_count_threshold = 24
      }
      liveness_probe {
        transport               = "TCP"
        port                    = 50051
        interval_seconds        = 10
        failure_count_threshold = 3
      }
    }
  }
}

resource "azurerm_container_app" "web" {
  name                         = local.web_name
  resource_group_name          = var.resource_group_name
  container_app_environment_id = var.environment_id
  revision_mode                = var.revision_mode
  workload_profile_name        = "Consumption"
  tags                         = var.tags

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
    max_replicas = var.max_replicas

    container {
      name   = "web"
      image  = var.web_image
      cpu    = 0.5
      memory = "1Gi"

      env {
        name  = "API_HOST"
        value = local.api_fqdn
      }
      env {
        name  = "API_PORT"
        value = "443"
      }
      env {
        name  = "API_TLS"
        value = "true"
      }
      env {
        name  = "PUBLIC_HOST"
        value = local.web_fqdn
      }

      startup_probe {
        transport               = "HTTP"
        port                    = 8080
        path                    = "/health"
        interval_seconds        = 5
        failure_count_threshold = 36
      }
      liveness_probe {
        transport               = "HTTP"
        port                    = 8080
        path                    = "/health"
        interval_seconds        = 10
        failure_count_threshold = 3
      }
      readiness_probe {
        transport               = "HTTP"
        port                    = 8080
        path                    = "/ready"
        interval_seconds        = 10
        failure_count_threshold = 3
      }
    }
  }
}
