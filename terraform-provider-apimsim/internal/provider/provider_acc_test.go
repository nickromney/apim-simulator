package provider

import (
	"fmt"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/providerserver"
	"github.com/hashicorp/terraform-plugin-go/tfprotov6"
	"github.com/hashicorp/terraform-plugin-testing/helper/acctest"
	"github.com/hashicorp/terraform-plugin-testing/helper/resource"
)

// Acceptance tests need TF_ACC=1, a terraform binary, and a running simulator
// at APIMSIM_ENDPOINT (with APIMSIM_TENANT_KEY if tenant access is on).
// `make testacc` starts one. After every apply step the harness refreshes and
// plans again, and fails on a non-empty plan: that is the drift check.

var factories = map[string]func() (tfprotov6.ProviderServer, error){
	"apimsim": providerserver.NewProtocol6WithError(New("test")()),
}

func TestAccCatalogue(t *testing.T) {
	suffix := acctest.RandStringFromCharSet(6, acctest.CharSetAlphaNum)
	full := catalogueConfig(suffix, `description = "Starter plan"`)
	cleared := catalogueConfig(suffix, "")

	resource.Test(t, resource.TestCase{
		ProtoV6ProviderFactories: factories,
		Steps: []resource.TestStep{
			{
				Config: full,
				Check: resource.ComposeAggregateTestCheckFunc(
					resource.TestCheckResourceAttr("apimsim_product.starter", "id", "starter-"+suffix),
					resource.TestCheckResourceAttr("apimsim_product.starter", "state", "published"),
					resource.TestCheckResourceAttr("apimsim_product.starter", "require_subscription", "true"),
					resource.TestCheckResourceAttrSet("apimsim_product.starter", "resource_id"),
					resource.TestCheckResourceAttr("apimsim_api.echo", "protocols.#", "2"),
					resource.TestCheckResourceAttr("apimsim_api.echo", "products.0", "starter-"+suffix),
					resource.TestCheckResourceAttr("apimsim_api_operation.create", "id", "echo-"+suffix+"/create"),
					resource.TestCheckResourceAttr("apimsim_api_operation.create", "method", "POST"),
					resource.TestCheckResourceAttr("apimsim_backend.primary", "basic_password", "hunter2"),
					resource.TestCheckResourceAttr("apimsim_named_value.token", "secret", "true"),
				),
			},
			{
				// Removing an optional attribute must clear it on the server.
				// Products merge a PUT; the explicit null the provider sends
				// still clears the field.
				Config: cleared,
				Check:  resource.TestCheckNoResourceAttr("apimsim_product.starter", "description"),
			},
			importStep("apimsim_product.starter"),
			importStep("apimsim_api.echo"),
			importStep("apimsim_api_operation.create"),
			importStep("apimsim_api_policy.echo"),
			importStep("apimsim_backend.primary", "basic_password"),
			importStep("apimsim_named_value.token", "value"),
			importStep("apimsim_tag.public"),
			importStep("apimsim_policy_fragment.correlation"),
		},
	})
}

// TestAccAPIUpdateKeepsPolicy: renaming an API must not reset the policy that
// apimsim_api_policy manages, or the next plan would want to put it back.
func TestAccAPIUpdateKeepsPolicy(t *testing.T) {
	suffix := acctest.RandStringFromCharSet(6, acctest.CharSetAlphaNum)
	config := func(name string) string {
		return fmt.Sprintf(`
resource "apimsim_api" "gap" {
  api_id            = "gap-%[1]s"
  name              = %[2]q
  path              = "gap-%[1]s"
  upstream_base_url = "http://backend.example"
}

resource "apimsim_api_policy" "gap" {
  api_id = apimsim_api.gap.api_id
  xml    = %[3]q
}
`, suffix, name, policyXML)
	}

	resource.Test(t, resource.TestCase{
		ProtoV6ProviderFactories: factories,
		Steps: []resource.TestStep{
			{Config: config("Gap")},
			{
				Config: config("Gap renamed"),
				Check:  resource.TestCheckResourceAttr("apimsim_api_policy.gap", "xml", policyXML),
			},
		},
	})
}

// TestAccOperationClearsDescription: removing an attribute from configuration
// must clear it on the server, not leave the stored value behind.
func TestAccOperationClearsDescription(t *testing.T) {
	suffix := acctest.RandStringFromCharSet(6, acctest.CharSetAlphaNum)
	config := func(description string) string {
		return fmt.Sprintf(`
resource "apimsim_api" "gap" {
  api_id            = "opgap-%[1]s"
  name              = "Gap"
  path              = "opgap-%[1]s"
  upstream_base_url = "http://backend.example"
}

resource "apimsim_api_operation" "gap" {
  api_id       = apimsim_api.gap.api_id
  operation_id = "list"
  name         = "List"
  url_template = "/items"
  %[2]s
}
`, suffix, description)
	}

	resource.Test(t, resource.TestCase{
		ProtoV6ProviderFactories: factories,
		Steps: []resource.TestStep{
			{Config: config(`description = "List items"`)},
			{
				Config: config(""),
				Check:  resource.TestCheckNoResourceAttr("apimsim_api_operation.gap", "description"),
			},
		},
	})
}

func importStep(address string, writeOnly ...string) resource.TestStep {
	return resource.TestStep{
		ResourceName:            address,
		ImportState:             true,
		ImportStateVerify:       true,
		ImportStateVerifyIgnore: writeOnly,
	}
}

const policyXML = `<policies><inbound><base /><set-header name="x-from" exists-action="override"><value>terraform</value></set-header></inbound><backend><base /></backend><outbound><base /></outbound><on-error><base /></on-error></policies>`

func catalogueConfig(suffix, productDescription string) string {
	return fmt.Sprintf(`
resource "apimsim_product" "starter" {
  product_id = "starter-%[1]s"
  name       = "Starter"
  state      = "published"
  %[2]s
}

resource "apimsim_api" "echo" {
  api_id            = "echo-%[1]s"
  name              = "Echo"
  path              = "echo-%[1]s"
  upstream_base_url = "http://backend.example"
  products          = [apimsim_product.starter.product_id]
}

resource "apimsim_api_operation" "create" {
  api_id       = apimsim_api.echo.api_id
  operation_id = "create"
  name         = "Create item"
  method       = "POST"
  url_template = "/items"
  description  = "Create an item"
}

resource "apimsim_api_policy" "echo" {
  api_id = apimsim_api.echo.api_id
  xml    = %[3]q
}

resource "apimsim_backend" "primary" {
  backend_id     = "primary-%[1]s"
  url            = "http://backend.example"
  auth_type      = "basic"
  basic_username = "svc"
  basic_password = "hunter2"
}

resource "apimsim_named_value" "token" {
  named_value_id = "token-%[1]s"
  display_name   = "token-%[1]s"
  value          = "s3cr3t"
  secret         = true
}

resource "apimsim_tag" "public" {
  tag_id       = "public-%[1]s"
  display_name = "Public"
}

resource "apimsim_policy_fragment" "correlation" {
  fragment_id = "correlation-%[1]s"
  xml         = "<fragment><set-header name=\"x-correlation\" exists-action=\"skip\"><value>@(context.RequestId.ToString())</value></set-header></fragment>"
}
`, suffix, productDescription, policyXML)
}
