terraform {
  required_version = ">= 1.9.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "8.12.0"
    }
  }
}

# Usa tus credenciales de administración (Application Default Credentials:
# `gcloud auth application-default login`). El lab en ejecución NO las usa: corre con la
# cuenta de servicio mínima que crea este módulo.
provider "google" {
  project = var.project_id
  region  = var.region

  # Necesario para la API de presupuestos cuando se usan credenciales de usuario.
  billing_project       = var.project_id
  user_project_override = true

  default_labels = {
    project    = "agentic-lab"
    managed-by = "terraform"
  }
}
