# Terraform provider for the APIM simulator (prototype)

Manage a running simulator's products, APIs, operations, policies, backends,
named values, tags, policy fragments, product–API links and subscriptions
with Terraform or OpenTofu. Plans show
drift when someone changes the simulator through the console or the management
API, and `terraform destroy` removes what the configuration created.

This is a prototype. It is not published to a registry; you build it from this
checkout and point Terraform at the build.

## Before you start

You need:

- Go 1.27 or later (`go version`)
- Terraform or OpenTofu (`terraform version` or `tofu version`)
- `uv`, to run the simulator for the acceptance tests

## Try the example

Start a simulator from a config file. The provider manages API-authored
configs only, so a simulator started from environment variables alone (which
builds a legacy route config) refuses API changes.

```bash
make build
```

```bash
make example-plan
```

`example-plan` writes `bin/dev.tfrc`, a CLI config with a `dev_overrides`
entry for this build, and runs `terraform plan` on
[examples/main.tf](examples/main.tf). Expected result: a warning that provider
development overrides are in effect, then `Plan: 5 to add`. The plan needs no
running simulator; an apply does.

To apply, start a simulator on port 8000 and use the same CLI config:

```bash
TF_CLI_CONFIG_FILE="$PWD/bin/dev.tfrc" terraform -chdir=examples apply
```

OpenTofu reads the same file: replace `terraform` with `tofu`.

## Configure the provider

| Attribute | Environment variable | Default |
| --- | --- | --- |
| `endpoint` | `APIMSIM_ENDPOINT` | `http://localhost:8000` |
| `tenant_key` | `APIMSIM_TENANT_KEY` | none; sent as `X-Apim-Tenant-Key` |

## Resources

| Resource | Import ID |
| --- | --- |
| `apimsim_product` | `{product_id}` |
| `apimsim_api` | `{api_id}` |
| `apimsim_api_operation` | `{api_id}/{operation_id}` |
| `apimsim_api_policy` | `{api_id}` |
| `apimsim_backend` | `{backend_id}` |
| `apimsim_named_value` | `{named_value_id}` |
| `apimsim_tag` | `{tag_id}` |
| `apimsim_policy_fragment` | `{fragment_id}` |
| `apimsim_product_api` | `{product_id}/{api_id}` |
| `apimsim_subscription` | `{subscription_id}` |

```bash
terraform import apimsim_api_operation.get_item echo/get-item
```

Secrets (`apimsim_backend.basic_password`, `apimsim_named_value.value` when
`secret = true`) come back masked. Terraform keeps the configured value, so it
cannot see a secret changed outside Terraform, and an import leaves it empty.

Subscription keys are different: the simulator returns them, so
`primary_key` and `secondary_key` are sensitive values it generates when you
leave them out, and a key rotated outside Terraform shows up in state.

Link an API to a product with either `products` on `apimsim_api` or
`apimsim_product_api`, not both. Left out, `products` keeps whatever links the
simulator holds; set it to `[]` to remove them all.

## Known gaps

- **A simulator started without `APIM_CONFIG_PATH` refuses API changes.** It
  routes `/api` with a legacy route built from environment variables. Start
  it from a config file, as `make testacc` does.

Behaviour the simulator keeps by design is listed in `KNOWN_GAPS` in
[tests/test_management_round_trip.py](../tests/test_management_round_trip.py).

## Develop

```bash
make check
```

Runs `gofmt`, `go vet` and the unit tests.

```bash
make testacc
```

Starts a throwaway simulator on a free port, runs the acceptance tests against
it with a real `terraform` binary, then stops it. Every apply step is followed
by a refresh and a plan that must be empty, which is the drift check.

### Adding a resource

A resource is a model struct plus a `spec` entry in
[internal/provider/resources.go](internal/provider/resources.go). Struct tags
say where each field goes:

```go
type tagModel struct {
    ID          types.String `tfsdk:"id"`                          // path params joined by "/"
    TagID       types.String `tfsdk:"tag_id" apim:"path"`          // URL segment
    DisplayName types.String `tfsdk:"display_name"`                // PUT body and GET
    ResourceID  types.String `tfsdk:"resource_id" apim:"read_only"` // GET only
}
```

The `spec` gives the URL template (`tags/{tag_id}`), the docs and the schema of
the body attributes. The `id` attribute and the path-parameter attributes are
added for you, and `TestEveryResourceSchemaMatchesItsModel` fails if the schema
and the model disagree. Give every attribute the server defaults the same
default in the schema, so removing it from configuration resets it. Where the
server nests what the model keeps flat, give the spec a `decode` function,
as `apimsim_subscription` does for its keys.
