output "dashboard_id" {
  description = "Id del documento del dashboard (ábrelo en la app Dashboards)."
  value       = dynatrace_document.lab_dashboard.id
}

output "anomaly_detectors" {
  value = { for k, d in dynatrace_davis_anomaly_detectors.lab : k => d.title }
}
