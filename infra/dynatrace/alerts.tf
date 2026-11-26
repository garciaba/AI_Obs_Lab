# Alertas de latencia y de errores del agente y del servidor MCP: detectores de anomalías
# de Davis con umbral estático sobre consultas DQL de spans (funcionan con servicios
# ingeridos solo por OTLP, sin depender de las métricas dt.service.*).

locals {
  detectors = {
    agent-latency = {
      title       = "Agentic lab: latencia alta del agente"
      description = "La latencia media de POST /chat supera ${var.agent_latency_threshold_ms} ms."
      threshold   = var.agent_latency_threshold_ms
      event_type  = "SLOWDOWN"
      query       = <<-DQL
        fetch spans
        | filter service.name == "${var.agent_service}" and span.kind == "server" and endpoint.name == "POST /chat"
        | fieldsAdd duration_ms = toLong(duration) / 1000000.0
        | makeTimeseries latency_ms = avg(duration_ms), interval:1m
      DQL
    }
    mcp-latency = {
      title       = "Agentic lab: latencia alta del servidor MCP"
      description = "La latencia media de las herramientas MCP supera ${var.mcp_latency_threshold_ms} ms (escenario slow-tool)."
      threshold   = var.mcp_latency_threshold_ms
      event_type  = "SLOWDOWN"
      query       = <<-DQL
        fetch spans
        | filter service.name == "${var.mcp_service}" and isNotNull(gen_ai.tool.name)
        | fieldsAdd duration_ms = toLong(duration) / 1000000.0
        | makeTimeseries latency_ms = avg(duration_ms), interval:1m
      DQL
    }
    agent-errors = {
      title       = "Agentic lab: errores en el agente"
      description = "Peticiones del agente con error (HTTP 5xx, fallos del LLM o de dependencias)."
      threshold   = var.error_count_threshold
      event_type  = "ERROR"
      query       = <<-DQL
        fetch spans
        | filter service.name == "${var.agent_service}" and span.status_code == "error"
        | makeTimeseries errors = count(default: 0), interval:1m
      DQL
    }
    mcp-errors = {
      title       = "Agentic lab: errores en el servidor MCP"
      description = "Herramientas MCP con error (escenarios tool-error y db-down)."
      threshold   = var.error_count_threshold
      event_type  = "ERROR"
      query       = <<-DQL
        fetch spans
        | filter service.name == "${var.mcp_service}" and span.status_code == "error"
        | makeTimeseries errors = count(default: 0), interval:1m
      DQL
    }
  }
}

resource "dynatrace_davis_anomaly_detectors" "lab" {
  for_each    = local.detectors
  title       = each.value.title
  description = each.value.description
  enabled     = true
  source      = "Terraform (agentic-lab)"

  analyzer {
    name = "dt.statistics.ui.anomaly_detection.StaticThresholdAnomalyDetectionAnalyzer"
    input {
      analyzer_input_field {
        key   = "query"
        value = trimspace(each.value.query)
      }
      analyzer_input_field {
        key   = "threshold"
        value = tostring(each.value.threshold)
      }
      analyzer_input_field {
        key   = "alertCondition"
        value = "ABOVE"
      }
      analyzer_input_field {
        key   = "alertOnMissingData"
        value = "false"
      }
      # Salta con 2 minutos por encima del umbral dentro de una ventana de 3: los
      # escenarios del lab duran pocos minutos.
      analyzer_input_field {
        key   = "slidingWindow"
        value = "3"
      }
      analyzer_input_field {
        key   = "violatingSamples"
        value = "2"
      }
      analyzer_input_field {
        key   = "dealertingSamples"
        value = "3"
      }
    }
  }

  event_template {
    properties {
      property {
        key   = "event.type"
        value = each.value.event_type
      }
      property {
        key   = "event.name"
        value = each.value.title
      }
      property {
        key   = "event.description"
        value = each.value.description
      }
      property {
        key   = "lab"
        value = "agentic-lab"
      }
    }
  }

  execution_settings {
    actor = var.alert_actor != "" ? var.alert_actor : null
  }
}
