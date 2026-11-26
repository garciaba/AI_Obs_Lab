variable "dt_env_url" {
  description = "URL del entorno Dynatrace: https://<id-entorno>.live.dynatrace.com"
  type        = string
}

variable "dt_config_token" {
  description = "Platform token para Terraform (DT_CONFIG_TOKEN). Permisos en docs/runbook.md."
  type        = string
  sensitive   = true
}

variable "agent_service" {
  description = "service.name del agente."
  type        = string
  default     = "wonder-toys-agent"
}

variable "mcp_service" {
  description = "service.name del servidor MCP."
  type        = string
  default     = "wonder-toys-mcp"
}

variable "agent_latency_threshold_ms" {
  description = "Latencia media por minuto del agente (ms) a partir de la cual salta la alerta."
  type        = number
  default     = 60000
}

variable "mcp_latency_threshold_ms" {
  description = "Latencia media por minuto de las herramientas MCP (ms). El escenario slow-tool añade 8000 ms."
  type        = number
  default     = 3000
}

variable "error_count_threshold" {
  description = "Spans de servidor con error por minuto a partir de los cuales salta la alerta."
  type        = number
  default     = 1
}

variable "alert_actor" {
  description = "UUID del service user con el que se ejecutan las consultas de las alertas. Vacío = el propietario del token."
  type        = string
  default     = ""
}

variable "dashboard_private" {
  description = "Si el dashboard es privado del propietario del token."
  type        = bool
  default     = false
}

variable "enable_openpipeline" {
  description = <<-EOT
    Crea el pipeline de spans del lab en OpenPipeline y SU TABLA DE RUTAS. Atención: el
    recurso de rutas sustituye toda la tabla de rutas de spans del entorno. Actívalo solo
    en un entorno donde Terraform sea el único que gestiona esas rutas.
  EOT
  type        = bool
  default     = false
}
