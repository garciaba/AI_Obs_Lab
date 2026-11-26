# IAM mínima para el lab: un usuario que solo puede invocar los dos perfiles de inferencia
# EU (Sonnet 5.5 y Haiku 5.5) y los modelos a los que esos perfiles enrutan.

data "aws_caller_identity" "current" {}

# El propio perfil dice a qué modelos (y en qué regiones) enruta: así la política no
# lleva regiones escritas a mano y sigue siendo correcta si AWS cambia los destinos.
data "aws_bedrock_inference_profile" "lab" {
  for_each             = toset([var.supervisor_inference_profile, var.subagent_inference_profile])
  inference_profile_id = each.value
}

locals {
  inference_profile_arns = [
    for p in data.aws_bedrock_inference_profile.lab : p.inference_profile_arn
  ]
  foundation_model_arns = distinct(flatten([
    for p in data.aws_bedrock_inference_profile.lab : [for m in p.models : m.model_arn]
  ]))
}

data "aws_iam_policy_document" "invoke" {
  statement {
    sid       = "InvokeLabInferenceProfiles"
    effect    = "Allow"
    actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = local.inference_profile_arns
  }

  # Los modelos base solo se pueden invocar a través de esos dos perfiles.
  statement {
    sid       = "InvokeModelsOnlyThroughLabProfiles"
    effect    = "Allow"
    actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = local.foundation_model_arns

    condition {
      test     = "StringLike"
      variable = "bedrock:InferenceProfileArn"
      values   = local.inference_profile_arns
    }
  }
}

resource "aws_iam_user" "lab" {
  name          = var.iam_user_name
  path          = "/lab/"
  force_destroy = true
}

resource "aws_iam_user_policy" "invoke" {
  name   = "invoke-lab-inference-profiles"
  user   = aws_iam_user.lab.name
  policy = data.aws_iam_policy_document.invoke.json
}

# La clave queda en el estado local de Terraform (fuera de Git) y `task up` la escribe en
# .lab/aws/credentials, que es lo que montan los contenedores.
resource "aws_iam_access_key" "lab" {
  user = aws_iam_user.lab.name
}

# Presupuesto con alarma: el único coste variable del lab son los tokens de Bedrock.
resource "aws_budgets_budget" "lab" {
  name         = "agentic-lab-bedrock"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "cost_filter" {
    for_each = length(var.budget_services) > 0 ? [1] : []
    content {
      name   = "Service"
      values = var.budget_services
    }
  }

  dynamic "notification" {
    for_each = length(var.budget_alert_emails) > 0 ? [80, 100] : []
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = notification.value == 100 ? "FORECASTED" : "ACTUAL"
      subscriber_email_addresses = var.budget_alert_emails
    }
  }
}
