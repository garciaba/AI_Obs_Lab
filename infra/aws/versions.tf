terraform {
  required_version = ">= 1.9.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.74.0"
    }
  }
}

# Usa las credenciales del perfil AWS_PROFILE (tu perfil de administración o SSO); el
# lab en ejecución NO usa ese perfil, sino el usuario mínimo que crea este módulo.
provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      project    = "agentic-lab"
      managed-by = "terraform"
    }
  }
}
