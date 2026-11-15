variable "name_suffix" {
  type        = string
  description = "Appended to the app names: hardware-api-<suffix> and hardware-web-<suffix>."
}

variable "resource_group_name" {
  type = string
}

variable "environment_id" {
  type        = string
  description = "Container Apps environment (workload-profiles, not express: the API needs HTTP/2 ingress)."
}

variable "environment_default_domain" {
  type        = string
  description = "The environment's default domain; app hostnames are <app-name>.<this>."
}

variable "api_image" {
  type        = string
  description = "gRPC API image, ideally pinned by digest: ghcr.io/owner/repo@sha256:..."
}

variable "web_image" {
  type        = string
  description = "Website image (Nginx + Envoy), ideally pinned by digest."
}

variable "revision_mode" {
  type    = string
  default = "Single"
}

variable "min_replicas" {
  type    = number
  default = 1
}

variable "max_replicas" {
  type    = number
  default = 1
}

variable "api_env" {
  type        = map(string)
  default     = {}
  description = "Plain environment variables for the API (ENVIRONMENT, MONGO_DATABASE, ...)."
}

variable "api_secret_env" {
  type        = map(string)
  sensitive   = true
  default     = {}
  description = "Secret environment variables for the API, ENV_NAME => value (MONGO_URI, TEAM_CODE_*, ...)."
}

variable "tags" {
  type    = map(string)
  default = {}
}
