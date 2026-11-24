# Azure APIM parity validation

Run identical deterministic API policies and requests against the simulator and
an existing Developer/Premium APIM. This lab creates one randomly named
`sim-parity-*` API, an API-scoped subscription, and `sim-parity-*` named values and policy fragments. It does not change existing
APIs, global policies, backends, or products, and makes no billable model calls.

## Run

Use the existing Azure CLI login for management authentication. Set deployment
identifiers through your environment; they are not fixture content:

```sh
export AZURE_APIM_SERVICE_ID='/subscriptions/<subscription>/resourceGroups/<group>/providers/Microsoft.ApiManagement/service/<service>'
export AZURE_APIM_BASE_URL='https://<service>.azure-api.net'
make -C examples/azure-validation prepare
make -C examples/azure-validation deploy
make -C examples/azure-validation serve
```

Leave the local server running, then in another terminal with the same Azure
base URL run:

```sh
make -C examples/azure-validation verify
make -C examples/azure-validation cleanup
```

`PARITY_SIMULATOR_PORT` changes the local listening port; set
`SIMULATOR_BASE_URL` correspondingly for verification. The default is 18891.
The lab intentionally leaves your existing port-8000 configuration alone.

The client uses the same API-scoped subscription key on both targets. Keys,
config, compiler diagnostics, and comparison reports live in ignored `.runtime`
files with restricted permissions. Nothing fetches keys from existing APIs.
Keep `.runtime` until cleanup completes; it records which resources are owned.
Cleanup deletes only its API, subscription, named value and fragment and retains evidence files.

If an inherited resource-type policy blocks policy fragments, set
`PARITY_SKIP_FRAGMENTS=1` for deployment. This excludes that case explicitly and
records the exclusion in `.runtime/excluded.json`; subsequent verification uses
the same reduced corpus. Set it to `0` to restore the full corpus. An excluded
case is not evidence of parity, and this lab never changes policy assignments.

## Core fragment cases

The corpus also replays the shipped [core reusable fragments](../policy-fragments/README.md).
The lab deploys copies named `<prefix>-core-*`, with their own `<prefix>-core-*`
named values, so an existing `core-*` fragment in the target APIM is never
read or changed.

| Cases | What they show |
| --- | --- |
| `core-rate-limit-1` to `-3`, `core-rate-limit-exceeded` | Three calls allowed with `x-ratelimit-remaining` 2, 1, 0, then 429 |
| `core-rate-limit-off` | A variable set before the include turns the limit off |
| `core-token-budget-unspent`, `-seed`, `-exhausted` | The check allows a fresh budget, a seed call spends it with `cache-store-value`, then the check returns 403 with `x-token-budget-remaining: 0` |
| `core-token-budget-off` | The check is skipped when turned off, even with the budget spent |
| `core-usage-*` | The token-usage expression from `core-token-budget-record`, run on request bodies |
| `core-subscription-coalesce`, `core-variable-*`, `core-convert-math`, `core-equals-ignore-case` | `??`, `?.`, `GetValueOrDefault`, `Convert`, `Math` and `StringComparison` as the fragments use them |

Each `prepare` gives the run a new `X-Parity-Run` value, and the counters and
budgets key on it, so a rerun starts from empty counters on both gateways.

These cases do not cover:

- `core-token-budget-record` in an outbound section. That needs a model
  response, and this lab has no backend. Only its expressions are replayed.
- The 429 body, which names the seconds until the limit renews. Only the
  status is compared.
- Timing. Azure stores `cache-store-value` asynchronously, so the
  `-exhausted` case can fail if Azure has not stored the seed in time. Treat
  that failure as a timing finding to investigate, not a policy mismatch.

`PARITY_SKIP_FRAGMENTS=1` also excludes these fragment cases. The expression
cases still run.

## Evidence and limits

On 2026-10-03, a fresh Developer-tier APIM in External VNet mode compiled and
executed all 23 cases, matching the simulator's expected statuses,
exact bodies and selected response headers. See [the live comparison report](results-2026-10-03.json).
The first run excluded fragments because an inherited resource-type allowlist
denied `Microsoft.ApiManagement/service/policyFragments`. The allowlist was then
extended through a reviewed Terraform plan adding only that resource type, and
the full corpus passed on rerun. No exemption was used; the Developer-only SKU
guard remained intact.
All 23 cases, including fragments, passed locally. This run exposed missing
`Regex.Replace` expression support, now implemented and covered by regression
tests. The original APIM was verified restored to Internal mode after testing
reachability; only the second service remains for Terraform teardown.

The initial cases cover literal responses, header reads/defaults/overrides and
deletions, query reads/overrides, matched path parameters, body preservation and
transformation, method changes, variables, conditional branches, response
headers, custom status codes, named values, fragments, JSON body expressions,
regular expressions, header validation, and on-error handling. They use terminal policies to avoid dependence
on an external echo service. Azure policy compilation and runtime comparison
are separate checks; compilation alone is not parity evidence.

Every request has an expected status and body, preventing matching failures such
as two 404s from being labelled success. Selected response headers are compared;
request IDs, gateway versions, dates and other generated infrastructure headers
are omitted. Response bodies are compared exactly. An exact mismatch remains a
finding, not an automatically accepted normalization.

`make verify-azure` also accepts custom `VERIFY_CASES`, separate JSON
`SIMULATOR_HEADERS` / `AZURE_APIM_HEADERS`, and optional `VERIFY_REPORT` output.
A nonempty JSON case array is required. This adapter compares gateway behaviour;
management calls use Azure ARM credentials and simulator tenant/operator
credentials through their respective management interfaces.

For a separate disposable Developer service, use the sibling
publiccloudexperiments `terraform/avm/apim-validation` root. It owns its own
resource group, /32-restricted NSG, dedicated subnet in the existing VNet, and
Developer-only Deny policy. Teardown uses its Terraform `down` target; the main
APIM is outside this isolated state.

For in-place reachability on an already-created service, the sibling APIM
Terraform root provides `apim_public_validation_enabled` (default false) and
`apim_validation_egress_ip`, with `apim_manage_network_in_place=true` to avoid
AzureRM network-mode replacement. It retains the current classic internal subnet,
allows only the selected client IPv4 /32 on HTTPS, and preserves Azure service
management/health rules. Disable the boolean using the same deployment inputs to
restore the private posture and remove both validation rules. Cleanup of fixture
resources and restoration of networking are separate lifecycle actions.

This baseline does not establish parity for rate-limit timing, caching,
backend/callout transports, managed identities, Azure service integrations,
workspace/SKU-specific capabilities, or the whole management API. Expand the
fixture corpus and retain dated live reports before making those claims.
