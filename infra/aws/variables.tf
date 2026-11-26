variable "aws_region" {
  description = "Región desde la que se invocan los perfiles de inferencia."
  type        = string
  default     = "eu-west-1"
}

variable "supervisor_inference_profile" {
  description = "Perfil de inferencia EU del supervisor (Claude Sonnet 5.5)."
  type        = string
  default     = "eu.anthropic.claude-sonnet-5-5"
}

variable "subagent_inference_profile" {
  description = "Perfil de inferencia EU de los subagentes y del juez (Claude Haiku 5.5)."
  type        = string
  default     = "eu.anthropic.claude-haiku-5-5"
}

variable "iam_user_name" {
  description = "Usuario IAM con el que el lab invoca Bedrock."
  type        = string
  default     = "agentic-lab-bedrock"
}

variable "budget_limit_usd" {
  description = "Presupuesto mensual del lab en USD."
  type        = number
  default     = 25
}

variable "budget_alert_emails" {
  description = "Correos que reciben la alarma del presupuesto. Vacío = presupuesto sin avisos."
  type        = list(string)
  default     = []
}

variable "budget_services" {
  description = <<-EOT
    Valores de la dimensión Service de Cost Explorer que cuenta el presupuesto. Lista
    vacía = toda la cuenta (recomendado en una cuenta dedicada al lab). Comprueba en Cost
    Explorer cómo aparecen tus cargos de Claude: según la cuenta figuran como
    "Amazon Bedrock" o con el nombre del modelo en AWS Marketplace.
  EOT
  type        = list(string)
  default     = ["Amazon Bedrock"]
}
