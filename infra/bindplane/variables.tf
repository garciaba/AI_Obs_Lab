variable "bindplane_remote_url" {
  description = "URL del Bindplane server tal como la ve Terraform (desde el host)."
  type        = string
  default     = "http://localhost:3001"
}

variable "bindplane_username" {
  description = "Usuario administrador de Bindplane (BINDPLANE_ADMIN_USER)."
  type        = string
}

variable "bindplane_password" {
  description = "Contraseña del administrador de Bindplane (BINDPLANE_ADMIN_PASSWORD)."
  type        = string
  sensitive   = true
}

variable "configuration_name" {
  description = "Nombre de la configuración. El colector se enlaza con la etiqueta configuration=<nombre>."
  type        = string
  default     = "agentic-lab"
}

variable "arize_space_id" {
  description = "Space ID de Arize AX (cabecera space_id)."
  type        = string
  sensitive   = true
}

variable "arize_api_key" {
  description = "API key de Arize AX (cabecera api_key)."
  type        = string
  sensitive   = true
}

variable "arize_project_name" {
  description = "Proyecto de Arize AX donde aterrizan las trazas."
  type        = string
  default     = "agentic-lab"
}

variable "arize_otlp_host" {
  description = "Host OTLP de Arize AX (gRPC, puerto 443). Cambiar para regiones EU/CA."
  type        = string
  default     = "otlp.arize.com"
}

variable "dt_env_url" {
  description = "URL del entorno Dynatrace, sin barra final: https://<id-entorno>.live.dynatrace.com"
  type        = string

  validation {
    condition     = can(regex("^https://[^/]+$", var.dt_env_url)) && !can(regex("\\.apps\\.", var.dt_env_url))
    error_message = "DT_ENV_URL debe ser https://<id-entorno>.live.dynatrace.com (sin .apps. y sin ruta)."
  }
}

variable "dt_ingest_token" {
  description = "Token de ingesta OTLP de Dynatrace (trazas, logs y métricas)."
  type        = string
  sensitive   = true
}

variable "dt_auth_scheme" {
  description = "Api-Token para access tokens (dt0c01.*), Bearer para platform tokens (dt0s16.*)."
  type        = string
  default     = "Api-Token"

  validation {
    condition     = contains(["Api-Token", "Bearer"], var.dt_auth_scheme)
    error_message = "DT_AUTH_SCHEME debe ser Api-Token o Bearer."
  }
}

variable "arize_root_decision_wait" {
  description = "Espera del tail sampling antes de decidir si una traza tiene spans de IA."
  type        = string
  default     = "30s"
}
