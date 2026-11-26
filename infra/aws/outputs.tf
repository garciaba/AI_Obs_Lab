output "lab_user_arn" {
  description = "ARN del usuario del lab; la puerta 0 comprueba que Bedrock responde con él."
  value       = aws_iam_user.lab.arn
}

output "access_key_id" {
  value     = aws_iam_access_key.lab.id
  sensitive = true
}

output "secret_access_key" {
  value     = aws_iam_access_key.lab.secret
  sensitive = true
}

output "aws_region" {
  value = var.aws_region
}

output "allowed_inference_profiles" {
  value = local.inference_profile_arns
}

output "allowed_foundation_models" {
  value = local.foundation_model_arns
}
