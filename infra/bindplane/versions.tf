terraform {
  required_version = ">= 1.9.0"

  required_providers {
    bindplane = {
      source  = "observiq/bindplane"
      version = "1.11.0"
    }
  }
}

# El proveedor habla con el Bindplane server del compose: por eso `task up` arranca
# primero el servidor y solo después aplica este módulo.
provider "bindplane" {
  remote_url = var.bindplane_remote_url
  username   = var.bindplane_username
  password   = var.bindplane_password
}
