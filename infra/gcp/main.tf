# IAM mínima para el lab: una cuenta de servicio que solo puede hacer predicciones en
# Vertex AI (invocar modelos), nada más del proyecto.

resource "google_project_service" "vertex" {
  project = var.project_id
  service = "aiplatform.googleapis.com"

  # Al destruir el lab no se desactiva la API: otros usos del proyecto pueden depender de ella.
  disable_on_destroy = false
}

resource "google_service_account" "lab" {
  project      = var.project_id
  account_id   = var.service_account_id
  display_name = "Agentic lab: invocación de Claude en Vertex AI"
}

# Rol propio con el único permiso que usan las llamadas a modelos de socios
# (rawPredict / streamRawPredict sobre publishers/anthropic/models/*). El rol predefinido
# roles/aiplatform.user daría además acceso a datasets, endpoints, pipelines, etc.
resource "google_project_iam_custom_role" "invoke" {
  project     = var.project_id
  role_id     = "agenticLabVertexInvoke"
  title       = "Agentic lab: invocar modelos de Vertex AI"
  description = "Solo predicción en Vertex AI (Claude en Model Garden)."
  permissions = ["aiplatform.endpoints.predict"]
}

resource "google_project_iam_member" "invoke" {
  project = var.project_id
  role    = google_project_iam_custom_role.invoke.id
  member  = google_service_account.lab.member
}

# La clave queda en el estado local de Terraform (fuera de Git) y `task up` la escribe en
# .lab/gcp/credentials.json, que es lo que montan los contenedores.
resource "google_service_account_key" "lab" {
  service_account_id = google_service_account.lab.name
}

# Presupuesto con alarma: el único coste variable del lab son los tokens de Vertex AI.
# Avisa por correo a los administradores de la cuenta de facturación al 80 % y al 100 %.
resource "google_billing_budget" "lab" {
  count           = var.billing_account != "" ? 1 : 0
  billing_account = var.billing_account
  display_name    = "agentic-lab"

  budget_filter {
    projects = ["projects/${data.google_project.lab.number}"]
  }

  amount {
    specified_amount {
      currency_code = var.budget_currency
      units         = tostring(var.budget_limit_usd)
    }
  }

  threshold_rules {
    threshold_percent = 0.8
  }

  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "FORECASTED_SPEND"
  }
}

data "google_project" "lab" {
  project_id = var.project_id
}
