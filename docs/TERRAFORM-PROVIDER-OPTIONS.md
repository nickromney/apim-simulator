# Terraform provider options

Maintainer note, 2026-11-26. It records how the simulator could be configured
with Terraform or OpenTofu, what was prototyped, and what each option would
cost. The working prototype is in
[terraform-provider-apimsim/](../terraform-provider-apimsim/README.md).

## Summary

| Option | What it is | State | Fits when |
| --- | --- | --- | --- |
| A. Generic REST provider | A community REST provider pointed at `/apim/management/*` | Not built | You want a one-hour diagnostic, not something to keep |
| B. ARM facade for `azapi` | Azure-shaped routes, so the real `azapi` provider deploys to the simulator | Spiked, blocked at TLS | The same HCL must run unchanged against Azure and locally |
| C. Native `apimsim` provider | Go provider on terraform-plugin-framework | Prototype working: 10 resources, acceptance tests pass | You want declarative config of the simulator itself, with drift detection |

Recommendation: continue with C, and treat the server gaps below as the real
work. C is already working end to end. B is possible, but each resource type
needs a translator and the TLS set-up is awkward on macOS. B's value, the same
HCL as production, is worth revisiting only for `azapi` users.

## What the contract test found

[tests/test_management_round_trip.py](../tests/test_management_round_trip.py)
checks what any declarative client needs from each writable resource:

- one URL per resource;
- a PUT that can be repeated and that replaces the resource;
- a GET that returns what the PUT accepted;
- unknown fields rejected;
- the resource gone after DELETE.

Known gaps run as strict xfails, so fixing one makes the suite fail until its
`KNOWN_GAPS` entry is removed.

Better than expected:

- Seventeen resources round-trip a full body through PUT and GET with no drift.
- A repeated PUT changes nothing.
- DELETE works everywhere except policies.

Fixed on this branch, with one rule: a PUT keeps a field it leaves out, and
an explicit `null` clears it.

- **An API or operation PUT no longer resets its policy.** `policies_xml` (and
  an operation's `tags`) is kept unless the body names it, so a policy managed
  through `/policies/...` survives an update to its parent. See
  `_given_or_kept` in `app/management_service.py`.
- **Operations and workspaces now clear a field sent as `null`.** Products
  already did. The provider sends every attribute, nulls included, so removing
  one from configuration clears it.

Kept by design: products, operations and workspaces still keep omitted
fields, because existing clients rely on it (a product PUT of `{"state":
"published"}` keeps its name and terms; an operation PUT keeps metadata from
an OpenAPI import). The contract test records this as `KEEPS_OMITTED`.

Also fixed on this branch:

- **Unknown fields are rejected with 422.** Request bodies derive from
  `ManagementBody` in `app/management_body.py`. It ignores the read-only
  fields a GET returns (`id`, `resource_id` and each resource's computed
  fields), so GET, edit, PUT still works, as with ARM.
- **One secret mask.** Every GET returns `***` for a secret, and a PUT that
  sends `***` back keeps the stored secret. Before, backends returned
  `[redacted]`, and sending a mask back saved it as the secret.
- **A backend PUT keeps its certificate settings.** Certificate files and
  verification flags come from config, and a PUT used to reset them.
- **Every resource has one URL with PUT, GET and DELETE:**
  - `PUT /subscriptions/{id}` creates or replaces a subscription. Its keys
    are kept unless the body sets them.
  - `GET /products/{product_id}/apis/{api_id}` reads a link.
  - `DELETE /policies/{scope_type}/{scope_name}` removes a policy, so the
    scope only inherits. A scope with no policy reads as the empty document.

Added with the product link and subscription resources (2026-11-27):

- **An API PUT keeps its product links.** `products` follows the same rule as
  `policies_xml`: kept when the body leaves it out, cleared by an explicit
  `null`. The provider leaves an unconfigured `products` out of the PUT, so a
  link made by `apimsim_product_api` survives an update to its API.
- **A subscription PUT that sends `***` as a key keeps the stored key.** It
  used to save `***` as the key. Subscription GETs return keys in plain text,
  so this only guards clients that mask them themselves.

Still open:

- **A simulator started from environment variables alone cannot be
  managed.** It routes `/api` with one legacy route, and route
  materialization drops legacy routes once any API exists. Fixing it needs
  wildcard operations (any method, any path), so the default route can
  become an API, or catalogues that mix routes and APIs. The error now says
  how to start the simulator from a config file instead.

## Option C: the prototype

The design keeps per-resource code to a declaration.

- `internal/client`: a small JSON client for `/apim/management/*`. It
  classifies errors (404 as `ErrNotFound`, other failures as `APIError` with
  FastAPI's detail) and decodes numbers exactly.
- `internal/provider/codec.go`: maps a model struct to its URL, PUT body and
  state, using `tfsdk` and `apim` struct tags (`path`, `read_only`). It sends
  nulls explicitly, keeps masked secrets, and turns unreturned computed values
  into nulls.
- `internal/provider/resource.go`: one generic CRUD and import implementation
  that every resource shares. It adds the `id` and path-parameter attributes.
- `internal/provider/resources.go`: the catalogue. A model and a spec per
  resource, with defaults matching the server's request models.

Tests:

- Unit tests cover the client, the codec, and a check that every resource's
  schema matches its model.
- `make testacc` starts a simulator and runs the acceptance tests with a real
  `terraform` binary. It covers create, a follow-up plan that must be empty,
  clearing a field, import of all ten resources, regressions for the two
  fixed gaps, and a product link that survives an update to its API.
- Both Terraform and OpenTofu planned the example through `dev_overrides`.

Not done yet:

- groups, users, version sets, diagnostics, loggers, schemas and
  workspaces;
- data sources;
- generated docs (`tfplugindocs`);
- a release pipeline.

`make local-ci` runs the provider's unit and acceptance tests through
`make test-terraform-provider`.

Each new resource is about 30 lines once its server behaviour passes the
contract test.

## Option B: the `azapi` facade spike

A subagent spiked this on 2026-11-26 with OpenTofu 1.13.10 and `azapi` 2.14.0,
against a throwaway shim. It used no real Azure.

What it verified:

- **`azapi` refuses plain HTTP.** Error: "authorized requests are not
  permitted for non-TLS protected (https) endpoints". This fires before any
  request is sent.
- **Self-signed certificates are not trusted on macOS.** Go uses the system
  trust store and ignores `SSL_CERT_FILE`, so the shim's certificate failed
  with `x509: certificate signed by unknown authority`. The workarounds would
  be a CA in the login keychain (mkcert), running Terraform in a Linux
  container, or a certificate from a trusted CA. None was tried.
- **Auth is easy.** With `use_msi = true`, `use_cli = false`,
  `IDENTITY_ENDPOINT` pointing at a local token route and `IDENTITY_HEADER`
  set, `azapi` fetched a token over plain HTTP and accepted an opaque one. The
  facade can ignore the token.
- **The custom environment is set through environment variables:**
  - `ARM_ENVIRONMENT=custom`
  - `ARM_RESOURCE_MANAGER_ENDPOINT`
  - `ARM_RESOURCE_MANAGER_AUDIENCE`
  - `ARM_ACTIVE_DIRECTORY_AUTHORITY_HOST`

  The documented `endpoint` block was rejected in 2.14.0.
- **The first request is a presence check.** It is
  `GET {parent}/namedValues/{name}?api-version=…`. With
  `skip_provider_registration = true` there were no subscription or provider
  calls before it.

Not verified: PUT body handling, synchronous versus asynchronous responses,
and round-trip drift. The spike stopped at TLS.

Cost, estimated:

- **Little existing code to reuse.** `terraform_import.py` imports plan JSON,
  and its `azapi` support covers only `Microsoft.ApiManagement/service` and
  MCP APIs.
- **A translator per resource type**, from ARM camelCase properties to the
  simulator's models and back, with policy XML parsed by
  `app.policy_rawxml.parse_policy_xml`.
- **About 1 to 2 weeks for 8 to 10 core types**, plus TLS and harness work.
- **`azurerm` would cost much more.** It needs a metadata host over HTTPS,
  long-running operations on the service resource, and many computed
  properties. Not recommended.

B and C share the server gaps above. A facade would also need faithful round
trips, so fixing those gaps helps either path.
