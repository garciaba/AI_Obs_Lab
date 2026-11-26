# Fuentes: lo que el colector recibe o lee.

# OTLP gRPC (4317) y HTTP (4318): trazas, logs y métricas del agente y del servidor MCP.
resource "bindplane_source" "otlp" {
  rollout      = true
  name         = "${var.configuration_name}-otlp"
  display_name = "OTLP de la aplicación"
  type         = "otlp"
  parameters_json = jsonencode([
    { name = "listen_address", value = "0.0.0.0" },
    { name = "grpc_port", value = 4317 },
    { name = "http_port", value = 4318 },
  ])
}

# Bindplane no tiene un tipo de fuente para logs de Docker ni para docker_stats: se usan
# fuentes `custom` con los receptores `filelog` y `docker_stats`, incluidos en DBDOT.
resource "bindplane_source" "docker_logs" {
  rollout      = true
  name         = "${var.configuration_name}-docker-logs"
  display_name = "Logs de contenedores Docker"
  type         = "custom"
  parameters_json = jsonencode([
    { name = "telemetry_types", value = ["Logs"] },
    { name = "configuration", value = local.fragment["source-docker-logs"] },
  ])
}

resource "bindplane_source" "docker_stats" {
  rollout      = true
  name         = "${var.configuration_name}-docker-stats"
  display_name = "Métricas de contenedores Docker"
  type         = "custom"
  parameters_json = jsonencode([
    { name = "telemetry_types", value = ["Metrics"] },
    { name = "configuration", value = local.fragment["source-docker-stats"] },
  ])
}
