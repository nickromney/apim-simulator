# Architecture pattern comparison — 2026-10-01

The local labs provision no Azure infrastructure. Their backends, adapters,
stamps, and gateways run in Docker. The existing Azure APIM service remains
subject to its normal billing; using it for a comparison does not make that
service free.

## Local coverage

| Scenario | Evidence |
| --- | --- |
| Gateway routing | Distinct catalog and order services behind one gateway endpoint. |
| Gateway offloading | Request/header transforms, internal cache, and backend call-count checks. |
| Gatekeeper | Valid JWT forwarding, missing/invalid token rejection, subject throttling, and rejected-request call counts. |
| Gateway aggregation | Composed orders/profile JSON, upstream failure handling, and trace retrieval. |
| Backends for Frontends | Existing web/mobile example; Docker smoke passed in both direct and two-gateway topologies. |
| Anti-Corruption Layer | Adapter domain mapping; failed or malformed legacy responses produce 502. |
| Deployment Stamps / Bulkhead | Separate tenant containers/pools, CPU/memory limits, unknown-tenant rejection, isolated failure and recovery. |

Run the labs through their own Makefiles:

```bash
make -C examples/architecture-patterns up smoke
make -C examples/architecture-patterns down
make -C examples/architecture-patterns-extra up smoke
make -C examples/architecture-patterns-extra down
```

Use the same `STACK_SLOT` on lifecycle and smoke commands if running multiple
labs simultaneously. Local smoke tests verify the backing HTTP services;
Azure mock fixtures do not prove those application or infrastructure roles.

## Azure target and runtime reachability

Validation used an existing Developer-tier APIM service in internal VNet mode.
Its deployment identifiers and addresses are omitted from this public report.
The saved JSON reports use `configured-apim-service` as a redacted target label;
the measured results are unchanged. Future harness reports omit deployment
identifiers and native error details that could contain them.

The local host could not resolve that gateway hostname. The initial inventory
showed no private execution path. A subsequent user-authorized validation
session used the sibling `publiccloudexperiments` repository's Terraform
`probe_only_session_override=true` to provision the private Container Apps
environment and connectivity probe without enabling its other workloads.
The probe resolved APIM to its private address and reached it over verified HTTPS.

All eleven shared runtime checks passed on Azure, matching the local outcomes.
This verifies the scoped policy behavior against deterministic native APIM
fixtures, not complete infrastructure equivalence for every pattern.

## Recorded results

- The full Python suite passed: **765 tests**, with one external-service
  integration test deselected. Python formatting/lint, changed Markdown/YAML
  lint, and both new Compose configurations passed.
  All 15 focused harness tests also passed after adding the private-runner callback.
- Both new Docker smoke flows passed repeatedly. The existing BFF Docker
  smoke passed in direct and two-gateway modes. The comparison harness's
  [11 local request cases](architecture-patterns-local-2026-10-01.json) passed,
  including backend-count evidence of a cache hit.
- Azure [accepted nine example XML policies](architecture-patterns-azure-2026-10-01.json):
  the four core API policies, the generated orders backend override, the
  adapter façade policy, and the three BFF public/internal policies. Mock
  fixture operation policies were also accepted as comparison scaffolding.
- All nine temporary APIs from the final Azure check were removed. A separate
  API-list query confirmed no `aps-` comparison APIs remained. Failed earlier
  validation attempts also cleaned up their temporary APIs.
- The [live Azure runtime report](architecture-patterns-azure-runtime-2026-10-01.json)
  records eleven passing checks through the private Container Apps probe:
  distinct routing, transformations, a cache hit proven by reused fixture
  request ID, JWT rejection/acceptance, subject throttling, and aggregation
  success, backend error and transport error. Its five temporary APIs were
  removed. Earlier console-transport attempts also removed their APIs.
- Terraform reversal destroyed all eleven session resources. Azure inventory
  confirmed no Container Apps or environments remained. The environment's
  asynchronous deletion took about 25 minutes; the probe, endpoint and DNS
  were removed earlier. Durable identities and ACR pull grants were retained.
  A final plan with no session overrides exited zero with **No changes**.

The supplemental simulator backend pools and circuit-breaker configuration
were exercised locally; they were not translated into or validated as Azure
backend ARM resources. BFF XML passed native policy validation and its Docker
smokes passed; the runtime comparison covers the four core gateway patterns.

## Comparison harness

[`azure_compare.py`](../examples/architecture-patterns/azure_compare.py) uses
the Azure CLI's existing session. Its default mode is a local dry-run. Explicit
execution creates UUID-named temporary APIs on the selected existing service
and checks ownership before cleanup. The policy-acceptance mode skips gateway
requests; runtime mode requires a reachable private gateway.

Use `make -C examples/architecture-patterns azure-plan` for the local dry-run.
The `azure-validate` and `azure-compare` targets require
`AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, and `AZURE_APIM_SERVICE`.
Set `AZURE_APIM_BASE_URL` for a custom reachable gateway origin and
`AZURE_REPORT` to save a report. `azure-compare` also requires the local lab
running in the same stack slot. XML from the supplemental lab and BFF example
is included in `azure-validate`; their backing applications are verified locally.

The runtime harness redirects backend URLs to deterministic mock operations on
the same APIM service. For an internal VNet gateway, its callouts use the
[documented loopback and Host-header workaround](https://learn.microsoft.com/en-us/azure/api-management/send-request-policy#usage-notes).
That keeps the cloud comparison independent of Container Apps and does not
expose the local simulator or backend services.

To repeat from the sibling repository, follow
`publiccloudexperiments/sites/docs/content/build/apim-pattern-validation.mdx`.
Its `scripts/verify-apim-patterns-aca.py` keeps Azure management credentials
local and sends the shared HTTP assertions through the probe console. It uses
the same temporary API ownership and cleanup path as this harness. The
Terraform session flag defaults to false; reverse the session after validation
to remove the environment, probe, endpoint and environment DNS while preserving
durable prerequisites. Azure resources incur charges while enabled.

## Issues found by the comparison

Tracing previously consumed `send-request` response bodies while recording
variable writes. Aggregation policies then received empty bodies when tracing
was enabled. Tracing now reads with `preserve_content=True`. Regression tests
verify traced and untraced outbound consumption, so tracing cannot change the
policy's response.

Azure rejected the gatekeeper JWT policy because its child elements were in
an order that the local parser accepted. The gatekeeper and existing BFF
examples now use signing keys, audiences, then issuers, following the
[native policy statement](https://learn.microsoft.com/en-us/azure/api-management/validate-jwt-policy#policy-statement).
The corrected XML passed Azure policy validation. The local parser still
accepts a broader range of element orders, so local parsing alone is not a
native schema check.

See the [catalogue inventory](ARCHITECTURE-PATTERNS.md) for the distinction
between APIM policy responsibilities and the supporting Azure services.
