output "configuration_name" {
  description = "Valor de la etiqueta `configuration` que debe llevar el colector."
  value       = bindplane_configuration_v2.lab.name
}

output "dynatrace_destination_type" {
  description = "Tipo de destino usado para Dynatrace según DT_AUTH_SCHEME."
  value       = bindplane_destination.dynatrace.type
}
