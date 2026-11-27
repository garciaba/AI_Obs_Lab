variable "project_id" {
  description = "Proyecto de Google Cloud donde se invoca Claude en Vertex AI (GCP_PROJECT_ID)."
  type        = string
}

variable "region" {
  description = "Región de Vertex AI desde la que se invocan los modelos (GCP_REGION)."
  type        = string
  default     = "europe-west1"
}

variable "service_account_id" {
  description = "Identificador de la cuenta de servicio con la que el lab invoca Vertex AI."
  type        = string
  default     = "agentic-lab-vertex"
}

variable "billing_account" {
  description = "Cuenta de facturación (XXXXXX-XXXXXX-XXXXXX) para el presupuesto. Vacío = sin presupuesto."
  type        = string
  default     = ""
}

variable "budget_limit_usd" {
  description = "Presupuesto mensual del proyecto del lab, en la moneda de la cuenta de facturación."
  type        = number
  default     = 25
}

variable "budget_currency" {
  description = "Moneda de la cuenta de facturación (debe coincidir con la suya)."
  type        = string
  default     = "USD"
}
