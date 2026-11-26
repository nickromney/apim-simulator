terraform {
  required_providers {
    apimsim = {
      source = "nickromney/apimsim"
    }
  }
}

# Reads APIMSIM_ENDPOINT and APIMSIM_TENANT_KEY when these are not set.
provider "apimsim" {
  endpoint = "http://localhost:8000"
}

resource "apimsim_product" "starter" {
  product_id  = "starter"
  name        = "Starter"
  description = "Free tier"
  state       = "published"
}

resource "apimsim_named_value" "greeting" {
  named_value_id = "greeting"
  display_name   = "greeting"
  value          = "hello from terraform"
}

resource "apimsim_api" "echo" {
  api_id            = "echo"
  name              = "Echo"
  path              = "echo"
  upstream_base_url = "http://mock-backend:8080"
  products          = [apimsim_product.starter.product_id]
}

resource "apimsim_api_operation" "get_item" {
  api_id       = apimsim_api.echo.api_id
  operation_id = "get-item"
  name         = "Get item"
  url_template = "/items/{id}"
}

resource "apimsim_api_policy" "echo" {
  api_id = apimsim_api.echo.api_id
  xml    = <<-XML
    <policies>
      <inbound>
        <base />
        <set-header name="x-greeting" exists-action="override">
          <value>{{greeting}}</value>
        </set-header>
      </inbound>
      <backend><base /></backend>
      <outbound><base /></outbound>
      <on-error><base /></on-error>
    </policies>
  XML

  # The simulator rejects a policy that names an unknown {{named value}}.
  depends_on = [apimsim_named_value.greeting]
}
