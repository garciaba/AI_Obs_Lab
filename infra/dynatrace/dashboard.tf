# Dashboard del lab (app Dashboards, documento de tipo "dashboard").

locals {
  agent = var.agent_service
  mcp   = var.mcp_service

  dql_tiles = [
    {
      title = "Agente: peticiones por resultado"
      viz   = "barChart"
      query = <<-DQL
        fetch spans
        | filter service.name == "${local.agent}" and span.kind == "server" and endpoint.name == "POST /chat"
        | makeTimeseries requests = count(), by:{http.response.status_code}, interval:1m
      DQL
    },
    {
      title = "Agente: latencia por petición (ms)"
      viz   = "lineChart"
      query = <<-DQL
        fetch spans
        | filter service.name == "${local.agent}" and span.kind == "server" and endpoint.name == "POST /chat"
        | fieldsAdd duration_ms = toLong(duration) / 1000000.0
        | makeTimeseries { p50 = percentile(duration_ms, 50), p90 = percentile(duration_ms, 90) }, interval:1m
      DQL
    },
    {
      title = "Servidor MCP: latencia por herramienta (ms)"
      viz   = "lineChart"
      query = <<-DQL
        fetch spans
        | filter service.name == "${local.mcp}" and isNotNull(gen_ai.tool.name)
        | fieldsAdd duration_ms = toLong(duration) / 1000000.0
        | makeTimeseries avg_ms = avg(duration_ms), by:{gen_ai.tool.name}, interval:1m
      DQL
    },
    {
      title = "Errores por servicio (spans con estado error)"
      viz   = "barChart"
      query = <<-DQL
        fetch spans
        | filter in(service.name, {"${local.agent}", "${local.mcp}"}) and span.status_code == "error"
        | makeTimeseries errors = count(), by:{service.name}, interval:1m
      DQL
    },
    {
      title = "Tokens por modelo"
      viz   = "barChart"
      query = <<-DQL
        fetch spans
        | filter service.name == "${local.agent}" and isNotNull(gen_ai.usage.input_tokens)
        | makeTimeseries { input = sum(gen_ai.usage.input_tokens), output = sum(gen_ai.usage.output_tokens) }, by:{gen_ai.request.model}, interval:5m
      DQL
    },
    {
      title = "Llamadas al LLM: latencia por modelo (ms)"
      viz   = "lineChart"
      query = <<-DQL
        fetch spans
        | filter service.name == "${local.agent}" and isNotNull(gen_ai.request.model)
        | fieldsAdd duration_ms = toLong(duration) / 1000000.0
        | makeTimeseries avg_ms = avg(duration_ms), by:{gen_ai.request.model}, interval:1m
      DQL
    },
    {
      title = "Bedrock: respuestas HTTP (throttling = 429)"
      viz   = "barChart"
      query = <<-DQL
        fetch spans
        | filter service.name == "${local.agent}" and span.kind == "client" and contains(server.address, "bedrock")
        | makeTimeseries calls = count(), by:{http.response.status_code}, interval:1m
      DQL
    },
    {
      title = "Postgres: consultas y errores"
      viz   = "lineChart"
      query = <<-DQL
        fetch spans
        | filter in(service.name, {"${local.agent}", "${local.mcp}"}) and isNotNull(db.system)
        | makeTimeseries { queries = count(), errors = countIf(span.status_code == "error") }, interval:1m
      DQL
    },
    {
      title = "Llamadas a herramientas por petición (media)"
      viz   = "lineChart"
      query = <<-DQL
        fetch spans
        | filter service.name == "${local.agent}" and isNotNull(wonder.tool_call_count)
        | makeTimeseries tool_calls = avg(wonder.tool_call_count), interval:1m
      DQL
    },
    {
      title = "Contenedores: memoria (bytes)"
      viz   = "lineChart"
      query = "timeseries memory = avg(container.memory.usage.total), by:{container.name}"
    },
    {
      title = "Logs de error y aviso (con trace_id)"
      viz   = "table"
      query = <<-DQL
        fetch logs
        | filter in(service.name, {"${local.agent}", "${local.mcp}"}) and in(loglevel, {"ERROR", "WARN"})
        | fields timestamp, service.name, loglevel, content, trace_id
        | sort timestamp desc
        | limit 50
      DQL
    },
    {
      title = "Peticiones más lentas"
      viz   = "table"
      query = <<-DQL
        fetch spans
        | filter service.name == "${local.agent}" and endpoint.name == "POST /chat"
        | fields start_time, duration, trace.id, session.id, wonder.tool_call_count, wonder.supervisor_model
        | sort duration desc
        | limit 20
      DQL
    },
  ]

  intro = <<-MD
    # Lab de IA agéntica — Wonder Toys
    Todo llega por OTLP desde el colector DBDOT gestionado por Bindplane. Los prompts y
    respuestas están redactados aquí: el contenido completo y las evaluaciones viven en
    Arize AX (mismo `trace.id`). Para abrir una traza concreta:
    `fetch spans | filter trace.id == toUid("<trace id>")`
  MD

  tiles = merge(
    { "0" = { type = "markdown", title = "", content = local.intro } },
    {
      for i, t in local.dql_tiles : tostring(i + 1) => {
        type                  = "data"
        title                 = t.title
        query                 = trimspace(t.query)
        visualization         = t.viz
        visualizationSettings = {}
        querySettings = {
          maxResultRecords       = 1000
          defaultScanLimitGbytes = 500
          maxResultMegaBytes     = 1
          defaultSamplingRatio   = 10
          enableSampling         = false
        }
      }
    }
  )

  # Rejilla de 24 columnas: introducción a todo el ancho y después dos tiles por fila.
  layouts = merge(
    { "0" = { x = 0, y = 0, w = 24, h = 4 } },
    {
      for i, t in local.dql_tiles : tostring(i + 1) => {
        x = (i % 2) * 12
        y = 4 + floor(i / 2) * 7
        w = 12
        h = 7
      }
    }
  )
}

resource "dynatrace_document" "lab_dashboard" {
  type    = "dashboard"
  name    = "Agentic lab — Wonder Toys"
  private = var.dashboard_private
  content = jsonencode({
    version   = 13
    variables = []
    tiles     = local.tiles
    layouts   = local.layouts
  })
}
