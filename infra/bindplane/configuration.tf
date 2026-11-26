# Configuración del colector: quién envía a quién.
#
#   otlp ──traces──┬─► [redacción] ───────────────────────────────► Dynatrace
#                  ├─► [solo spans de IA] ─────────────────────────► Arize AX
#                  └─► [tail sampling → raíz no-IA → CHAIN] ────────► Arize AX
#   otlp ──logs────────────────────────────────────────────────────► Dynatrace
#   otlp ──metrics──► [cumulative_to_delta] ───────────────────────► Dynatrace
#   docker logs ───────────────────────────────────────────────────► Dynatrace
#   docker stats ───► [cumulative_to_delta] ───────────────────────► Dynatrace
#
# Las rutas de Bindplane sustituyen a los conectores `forward` del cookbook de Arize:
# una ruta con varios componentes copia la señal a cada uno. Bindplane despliega la
# configuración al colector por OpAMP (`rollout = true`); el colector se enlaza con ella
# por la etiqueta configuration=<nombre> (OPAMP_LABELS en compose.yaml).

resource "bindplane_configuration_v2" "lab" {
  # Al quitar un componente y borrarlo en el mismo apply, primero se actualiza la
  # configuración y después se borra el componente.
  lifecycle {
    create_before_destroy = true
  }

  rollout     = true
  name        = var.configuration_name
  description = "Lab de IA agéntica: Arize AX (spans de IA) + Dynatrace (todo, redactado)"
  platform    = "linux"

  labels = {
    managed-by = "terraform"
    lab        = "agentic-lab"
  }

  source {
    name = bindplane_source.otlp.name

    route {
      route_id       = "otlp-traces"
      telemetry_type = "traces"
      components = [
        "processors/dynatrace-traces",
        "processors/arize-ai-spans",
        "processors/arize-root",
      ]
    }

    route {
      route_id       = "otlp-logs"
      telemetry_type = "logs"
      components     = ["destinations/dynatrace"]
    }

    route {
      route_id       = "otlp-metrics"
      telemetry_type = "metrics"
      components     = ["processors/metrics-delta"]
    }
  }

  source {
    name = bindplane_source.docker_logs.name

    route {
      route_id       = "docker-logs"
      telemetry_type = "logs"
      components     = ["destinations/dynatrace"]
    }
  }

  source {
    name = bindplane_source.docker_stats.name

    route {
      route_id       = "docker-metrics"
      telemetry_type = "metrics"
      components     = ["processors/metrics-delta"]
    }
  }

  # Rama Dynatrace: todos los spans, sin contenido de prompts ni respuestas.
  processor_group {
    route_id   = "dynatrace-traces"
    processors = [bindplane_processor.trace["transform-redact-ai-content"].name]

    route {
      route_id       = "dynatrace-traces-out"
      telemetry_type = "traces"
      components     = ["destinations/dynatrace"]
    }
  }

  # Rama Arize: solo los spans de IA, completos.
  processor_group {
    route_id   = "arize-ai-spans"
    processors = [bindplane_processor.trace["filter-ai-spans-only"].name]

    route {
      route_id       = "arize-ai-spans-out"
      telemetry_type = "traces"
      components     = ["destinations/arize"]
    }
  }

  # Rama raíz de Arize: la raíz HTTP de las trazas con spans de IA, marcada como CHAIN.
  processor_group {
    route_id = "arize-root"
    processors = [
      bindplane_processor.trace["tail-sampling-traces-with-ai-spans"].name,
      bindplane_processor.trace["filter-non-ai-trace-root"].name,
      bindplane_processor.trace["transform-root-as-chain"].name,
    ]

    route {
      route_id       = "arize-root-out"
      telemetry_type = "traces"
      components     = ["destinations/arize"]
    }
  }

  processor_group {
    route_id   = "metrics-delta"
    processors = [bindplane_processor.cumulative_to_delta.name]

    route {
      route_id       = "metrics-delta-out"
      telemetry_type = "metrics"
      components     = ["destinations/dynatrace"]
    }
  }

  destination {
    route_id   = "dynatrace"
    name       = bindplane_destination.dynatrace.name
    processors = [bindplane_processor.batch.name]
  }

  destination {
    route_id   = "arize"
    name       = bindplane_destination.arize.name
    processors = [bindplane_processor.trace["resource-arize-project"].name]
  }
}
