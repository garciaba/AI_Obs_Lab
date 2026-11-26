# Procesadores. La lógica del reparto de trazas es la del cookbook de Arize
# (dynatrace_dual_export) y vive en pipeline/*.yaml: los mismos fragmentos alimentan la
# configuración de referencia que valida el smoke test (render_reference.py).
#
# Bindplane no tiene tipo propio para tail_sampling, así que esa rama usa procesadores
# `custom`; el filtro y las transformaciones también, para que el YAML desplegado sea
# exactamente el validado. Ver docs/DECISIONS.md.

locals {
  fragment_files = [
    "filter-ai-spans-only",
    "tail-sampling-traces-with-ai-spans",
    "filter-non-ai-trace-root",
    "transform-root-as-chain",
    "transform-redact-ai-content",
    "resource-arize-project",
    "source-docker-logs",
    "source-docker-stats",
  ]

  # Fragmento sin líneas de comentario y con sus variables resueltas.
  fragment = {
    for name in local.fragment_files : name => replace(
      replace(
        replace(file("${path.module}/pipeline/${name}.yaml"), "/(?m)^\\s*#.*\\n/", ""),
        "$${ARIZE_PROJECT_NAME}", var.arize_project_name
      ),
      "decision_wait: 30s", "decision_wait: ${var.arize_root_decision_wait}"
    )
  }

  trace_processors = {
    "filter-ai-spans-only"               = "Arize: solo spans de IA"
    "tail-sampling-traces-with-ai-spans" = "Arize: trazas con algún span de IA"
    "filter-non-ai-trace-root"           = "Arize: solo la raíz no-IA"
    "transform-root-as-chain"            = "Arize: raíz marcada como CHAIN"
    "resource-arize-project"             = "Arize: proyecto de AX"
    "transform-redact-ai-content"        = "Dynatrace: redacción de prompts y respuestas"
  }
}

resource "bindplane_processor" "trace" {
  for_each     = local.trace_processors
  rollout      = true
  name         = "${var.configuration_name}-${each.key}"
  display_name = each.value
  type         = "custom"
  parameters_json = jsonencode([
    { name = "telemetry_types", value = ["Traces"] },
    { name = "configuration", value = local.fragment[each.key] },
  ])
}

resource "bindplane_processor" "batch" {
  rollout      = true
  name         = "${var.configuration_name}-batch"
  display_name = "Batch"
  type         = "batch"
  parameters_json = jsonencode([
    { name = "send_batch_size", value = 8192 },
    { name = "send_batch_max_size", value = 0 },
    { name = "timeout", value = "2s" },
  ])
}

# Dynatrace solo ingiere métricas delta. Con estado: una única instancia de colector.
resource "bindplane_processor" "cumulative_to_delta" {
  rollout      = true
  name         = "${var.configuration_name}-cumulative-to-delta"
  display_name = "Dynatrace: métricas en delta"
  type         = "cumulative_to_delta"
  parameters_json = jsonencode([
    { name = "max_staleness", value = "15m" },
  ])
}
