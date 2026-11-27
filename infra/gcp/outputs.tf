output "service_account_email" {
  description = "Cuenta de servicio del lab; la puerta 0 comprueba que Vertex AI responde con ella."
  value       = google_service_account.lab.email
}

output "credentials_json" {
  description = "Clave de la cuenta de servicio (JSON). Se escribe en .lab/gcp/credentials.json."
  value       = base64decode(google_service_account_key.lab.private_key)
  sensitive   = true
}

output "project_id" {
  value = var.project_id
}

output "region" {
  value = var.region
}
