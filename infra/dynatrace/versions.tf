terraform {
  required_version = ">= 1.9.0"

  required_providers {
    dynatrace = {
      source  = "dynatrace-oss/dynatrace"
      version = "1.109.0"
    }
  }
}

# DT_CONFIG_TOKEN es un platform token (dt0s16.*): los dashboards (documentos), los
# detectores de anomalías de Davis y OpenPipeline son APIs de plataforma.
provider "dynatrace" {
  dt_env_url     = var.dt_env_url
  platform_token = var.dt_config_token
}
