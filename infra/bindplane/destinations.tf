# Destinos.

locals {
  dt_host = regex("^https://([^/]+)$", var.dt_env_url)[0]
  # El id de entorno es la primera etiqueta del host (abc12345.live.dynatrace.com).
  dt_environment_id = split(".", local.dt_host)[0]

  # Access token (Api-Token): destino nativo `dynatrace_otlp` de Bindplane, que autentica
  # con el esquema Api-Token.
  dynatrace_native_parameters = [
    { name = "telemetry_types", value = ["Logs", "Metrics", "Traces"] },
    { name = "deployment_type", value = "Custom" },
    { name = "custom_url", value = "${var.dt_env_url}/api/v2/otlp" },
    { name = "your_environment_id", value = local.dt_environment_id },
    { name = "dynatrace_api_token", value = var.dt_ingest_token },
  ]

  # Platform token (Bearer): el destino nativo no admite ese esquema, así que se usa el
  # destino OTLP genérico por HTTP/protobuf con la cabecera Authorization.
  dynatrace_bearer_parameters = [
    { name = "telemetry_types", value = ["Logs", "Metrics", "Traces"] },
    { name = "hostname", value = local.dt_host },
    { name = "protocol", value = "http" },
    { name = "http_port", value = 443 },
    { name = "http_path_prefix", value = "/api/v2/otlp" },
    { name = "http_compression", value = "gzip" },
    { name = "enable_tls", value = true },
    { name = "headers", value = { Authorization = "Bearer ${var.dt_ingest_token}" } },
    # El batch lo pone el procesador compartido; se evita duplicarlo.
    { name = "batch_enabled", value = false },
  ]
}

# Dynatrace: todas las señales por OTLP/HTTP a ${DT_ENV_URL}/api/v2/otlp.
resource "bindplane_destination" "dynatrace" {
  rollout      = true
  name         = "${var.configuration_name}-dynatrace"
  display_name = "Dynatrace (OTLP/HTTP)"
  type         = var.dt_auth_scheme == "Api-Token" ? "dynatrace_otlp" : "otlp_grpc"
  # Se elige entre las dos cadenas JSON (las listas tienen formas distintas).
  parameters_json = (
    var.dt_auth_scheme == "Api-Token"
    ? jsonencode(local.dynatrace_native_parameters)
    : jsonencode(local.dynatrace_bearer_parameters)
  )
}

# Arize AX: solo trazas, por gRPC a otlp.arize.com:443 con las cabeceras space_id y api_key.
# (Bindplane trae también un destino `arize_ax`, que usa las cabeceras nuevas
# authorization / arize-space-id; ver docs/DECISIONS.md para cambiar a él.)
resource "bindplane_destination" "arize" {
  rollout      = true
  name         = "${var.configuration_name}-arize-ax"
  display_name = "Arize AX (OTLP/gRPC)"
  type         = "otlp_grpc"
  parameters_json = jsonencode([
    { name = "telemetry_types", value = ["Traces"] },
    { name = "hostname", value = var.arize_otlp_host },
    { name = "protocol", value = "grpc" },
    { name = "grpc_port", value = 443 },
    { name = "grpc_compression", value = "gzip" },
    { name = "enable_tls", value = true },
    { name = "headers", value = { space_id = var.arize_space_id, api_key = var.arize_api_key } },
    { name = "batch_enabled", value = true },
  ])
}
