# OpenPipeline (opcional, desactivado por defecto): segunda barrera de redacción. Si algún
# span del lab llegase a Dynatrace con contenido de prompts (por ejemplo, tras un cambio en
# el colector), este pipeline lo elimina antes de almacenarlo.

resource "dynatrace_openpipeline_v2_spans_pipelines" "lab" {
  count        = var.enable_openpipeline ? 1 : 0
  display_name = "Agentic lab: redacción de contenido de IA"
  custom_id    = "pipeline_agentic_lab_ai_redaction"

  processing {
    processors {
      processor {
        type        = "fieldsRemove"
        id          = "processor_agentic_lab_remove_ai_content"
        description = "Elimina entradas y salidas de los spans de IA"
        matcher     = "isNotNull(input.value) or isNotNull(output.value)"
        enabled     = true
        fields_remove {
          fields = ["input.value", "output.value", "gen_ai.input.messages", "gen_ai.output.messages", "gen_ai.system_instructions"]
        }
      }
    }
  }
}

# ATENCIÓN: este recurso sustituye TODA la tabla de rutas de spans del entorno.
resource "dynatrace_openpipeline_v2_spans_routing" "lab" {
  count = var.enable_openpipeline ? 1 : 0

  routing_entries {
    routing_entry {
      enabled       = true
      description   = "Spans del lab de IA agéntica"
      matcher       = "service.namespace == \"wonder-toys\""
      pipeline_type = "custom"
      pipeline_id   = dynatrace_openpipeline_v2_spans_pipelines.lab[0].id
    }
  }
}
