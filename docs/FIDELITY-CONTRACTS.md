# APIM fidelity contracts

This project implements a narrow, testable subset of Azure API Management. A
feature contract describes the observable behavior that the simulator promises
for that subset. It does not imply parity with every APIM tier, policy option,
management API, or Azure-hosted dependency.

## Status vocabulary

Every new contract in this document and in `contracts/contract_matrix.yml`
uses one of these statuses:

| Status | Promise |
| --- | --- |
| `supported` | The named subset follows the Microsoft Learn behavior reference. Inputs outside the named subset are rejected or explicitly excluded. The contract is backed by a local regression and a cited Microsoft reference. |
| `adapted` | The feature is useful locally, but one or more observable details intentionally differ from APIM. The difference is stated in the contract and must be covered by a local regression. |
| `unsupported` | The feature is outside the project. It must not be implied by a neighboring supported feature; where it reaches a parser or importer, rejection is preferred to silent approximation. |

The older capability matrix uses `Yes`, `Partial`, and `No` for historical
readability. `Yes` means an implemented subset; `Partial` means an incomplete
bounded capability and is not a fidelity verification; `No` means unsupported.
The contract matrix also retains legacy `partial` entries while owners migrate;
those entries are incomplete bounded capabilities, not verified contracts. New
contracts use the vocabulary above. A passing simulator test is evidence of the
local behavior; it is not evidence that Azure behaves the same way unless the
test is also compared with an official reference or an Azure fixture.

## Contract shape

Each contract records five things:

1. **Inputs and defaults**: accepted fields, default values, and supported
   versions or policy attributes.
2. **Order and observable result**: scope order, request processing order,
   response status/body/headers, and management projection.
3. **Errors and exclusions**: invalid inputs, unsupported options, and known
   local adaptations.
4. **Evidence**: a Microsoft Learn reference plus the local owner test or
   fixture named in `contract_matrix.yml`.
5. **Confidence**: `verified-subset` means the named behavior has local tests
   and a documentation-backed contract; `planned` means implementation work is
   still required.

## Products, publication, and subscriptions — verified subset

**Contract.** Product publication controls developer-portal discovery. It does
not invalidate subscription keys and does not deny an otherwise authorized
product-context gateway call. A valid product subscription is evaluated against
the API association and product policies whether the product is published or
not. API-scoped, all-APIs, and service-scoped subscriptions authorize without a
product context and therefore do not run product-scope policy.

**Inputs and defaults.** A product has `state: published | not_published`,
`require_subscription`, `approval_required`, and an API association. Products
authored without a state default to `published` for backwards-compatible local
configuration. Subscription keys use the configured API header/query names,
whose APIM defaults are `Ocp-Apim-Subscription-Key` and `subscription-key`; the
header wins when both are present. Only `active` subscriptions authenticate.

**Order and result.** Portal catalog filtering happens before portal sign-up:
an unpublished product is absent from discovery. Gateway authorization checks
subscription state, scope, API association, and product association; it does
not check `published` as an authorization gate. A product-scoped valid key
selects that product context. An open product can serve a request without a
key, while a key still supplies subscription context when present.

**Errors and exclusions.** Invalid, inactive, missing, or wrongly scoped keys
use the simulator's APIM-shaped 401 envelope. Portal sign-up can produce
`submitted` subscriptions when approval is required; the local portal identity
and email/notification flows are adapted. Group, user, all-APIs, and service
subscription behavior is limited to the local configuration model.

**Evidence.** [Subscriptions in APIM](https://learn.microsoft.com/en-us/azure/api-management/api-management-subscriptions),
[create and publish a product](https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-add-products),
and the portal visibility regression in `tests/test_portal.py`. Confidence:
`verified-subset`.

## OpenAPI import — verified subset

**Contract.** The importer implements a bounded, documentation-backed projection for
OpenAPI 2 JSON and OpenAPI 3.0.x (through 3.0.4). Accepting 3.0.4 is a local
extension: the current Microsoft getting-started tutorial points to a 3.0.4
Petstore document, while its import restrictions still list 3.0.3. OpenAPI 3.1 is outside this
contract and must be rejected or reported as unsupported until a separate
compatibility decision is made. The same projection is used by the
management import endpoint and Terraform/OpenTofu import.

**Inputs and defaults.** Accept inline or linked documents within the local
management/import surface. Select the first HTTPS `servers` URL; if none exists,
the upstream base URL is empty. Normalize newly created `operationId` to lower case,
replace runs of non-alphanumeric characters with a dash, trim dashes, and keep
the documented length/deduplication behavior. If `operationId` is absent,
derive it from method and path. Import `summary` as the display name, limited
to 300 characters, falling back to the operation ID. By default, translate
required query parameters into operation template parameters. GET, HEAD, and
OPTIONS request bodies are discarded.

Scalar parameter formats and numeric minimum/maximum constraints are retained
in API-scoped schemas. Query arrays support OpenAPI 3 `style: form`,
`explode: true` and Swagger 2 `collectionFormat: multi`; repeated query values
are forwarded unchanged and parameter validation coerces and checks every item.
Path/header arrays and other array encodings remain unsupported. Swagger form
and file parameters are projected into request representations' `form_parameters`;
file metadata uses a binary string schema. HTTP bodies are forwarded as received;
form decoding or form-field validation requires authored policy.

**Order and result.** Validate the document and URL templates, project API
metadata, operations, parameters, representations/examples, and API-scoped
schemas, then replace unmatched operations on update. On reimport, match the raw `operationId` against existing resource names.
Retain matching resource IDs and policies; otherwise generate a method/path ID
and copy policy when method/source path identify an existing operation,
independent of translated query parameters. A raw ID that differs from the
normalized resource name does not match in place on reimport; this follows the
Microsoft documentation and remains a priority for live Azure comparison. Management
projection and runtime routing must see the same imported metadata.

Inline request/response and parameter schemas are preserved under deterministic
generated API-scope schema IDs, with their internal references intact. This is
a local adaptation to Azure's documented restriction on inline operation
schemas. Default responses retain `status_code: default`, description, headers,
representations, examples, and schemas. Exact status metadata takes precedence;
mocking and response validation fall back to default metadata when no exact
status is declared. This fallback follows OpenAPI response semantics; its
Azure import projection remains unverified. Swagger form metadata projection
is also a local adaptation beyond Azure's documented unsupported form parameters.

**Errors and exclusions.** Reject unsupported versions, external
`$ref` files, recursive definitions, operation-level `servers`, object or
referenced parameter schemas, nested/non-query parameter arrays, unsupported
serialization, unprojected scalar parameter constraints, component request bodies/responses/
headers/examples, response status keys other than numeric or `default`, OpenAPI 3 TRACE operations,
callbacks, templated server URLs, `x-ms-paths`, OpenAPI 3.1 documents, and
documents over the documented inline size limit. Do not infer OpenAPI security
definitions as gateway authentication. Exact Azure error text is not promised
unless documented.

**Evidence.** [APIM API import restrictions](https://learn.microsoft.com/en-us/azure/api-management/api-management-api-import-restrictions).
Confidence: `verified-subset`. Local evidence includes `tests/test_openapi_import.py`,
`tests/test_openapi_workflows.py`, and `tests/test_import_mock_contract.py`.
Live Azure differential comparison remains unverified. Explicit local HTTP
backend overrides are a simulator adaptation. Unmodified snapshots of both
Microsoft tutorial Petstore specifications are covered by `tests/test_petstore_import.py`,
including management import, routing, repeated query arrays, constraint validation,
and default-response fallback. The fallback semantics reference the
[OpenAPI Responses Object](https://spec.openapis.org/oas/v3.0.4.html#responses-object).

## Policy expressions and policy scopes — verified subset

**Contract.** Policy XML is evaluated in the scope order global → product → API
→ operation. At each section, a child `<base />` inserts the parent section at
that position. An explicit section without `<base />` suppresses the parent
section; an omitted section inherits the parent in the simulator's documented
subset. Workspace scope is unsupported, and product selection follows local
subscription context. The effective-policy inspection endpoint expands
fragments and applies the same inheritance rules. Product context is explicit;
omitting it represents an API/all-APIs/service subscription. Other named-value
placeholders remain authored in the inspection view.

**Inputs and defaults.** Supported expressions are a focused C#-style subset:
single `@(...)` expressions, multi-statement `@{...}` blocks with explicit
returns, local declarations, assignment, `if`/`else`, ternaries, interpolation,
selected request/response/variable members, selected string/dictionary
members, and `JObject` body conversion. Request and response body reads follow
APIM's consuming default and `preserveContent` behavior for the supported types.

**Order and result.** Policy elements execute in XML order within a section;
`<base />` is replaced in place. Errors enter the supported `on-error` path
with the local `context.LastError` projection. Request/response headers and
query collections preserve repeated values in the local context.

**Errors and exclusions.** Unsupported C# 7 syntax, .NET types/methods, and
context members are rejected or reported as unsupported. Exact exception text
is only promised where Microsoft documents it. Workspace policies, complete
C# 7/.NET compatibility, and unmodeled APIM gateway context are unsupported.

**Evidence.** [Policy expressions](https://learn.microsoft.com/en-us/azure/api-management/api-management-policy-expressions),
[policy scopes](https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-policies),
and [set/edit policies](https://learn.microsoft.com/en-us/azure/api-management/set-edit-policies).
Confidence: `verified-subset`.

## Revisions and releases — local snapshot contract

**Contract.** Management-created revisions own independent API definitions,
operations, schemas, routing and policies. Ordinary requests use the current
API; `;rev=N` after the API suffix selects a saved revision. Version sets and
revisions remain distinct concepts.

**Inputs and defaults.** Creating an API through management upsert or OpenAPI
import creates online current revision 1. Creating another revision clones the
current definition and defaults to online and noncurrent. Revision PUT accepts
an optional partial `definition` update. Release PUT selects a revision and
optionally records notes.

**Order and result.** Direct current-API and operation edits synchronize its
snapshot. Explicit revision requests retain service-level policy context and
use only that revision's operations. Header/query version selectors disambiguate
APIs sharing a suffix. Making a revision current or creating its release selects
that snapshot for ordinary requests. Release notes appear in the local consumer
portal's change log.

**Errors and exclusions.** Offline and unknown revisions return 404. Invalid
snapshot data or policy XML returns 400. Noncurrent revisions reject changes to
the supported immutable API fields documented by Microsoft. Terraform-imported
revision metadata without complete snapshots does not reconstruct independent
runtime definitions; arbitrary source-API cloning, Azure control-plane resource
IDs, ETags, timestamps and full Azure portal publication infrastructure remain
outside this local contract.

**Evidence.** [APIM revisions](https://learn.microsoft.com/en-us/azure/api-management/api-management-revisions).
Focused gateway tests cover isolation, promotion, offline access, direct-current
edits and shared-path versions. The paired browser journey is recorded in
[portal UI pass](portal-ui-pass.md). Confidence: `verified-subset` for local
snapshots; imported revision metadata remains `adapted`.

## Certificates and cloud identity — verified local subset

**Contract.** Client certificate identity comes from actual TLS or an attested
TLS terminator. Configured identity claims use AND within one identity and OR
across identities. A local CA and signed CRL provide chain, validity and
revocation checks. The `validate-client-certificate` XML policy implements its
validation flags, identity selectors and ignore-error behavior; it is restricted
to inbound. Revocation checking requires a configured current signed local CRL.

**Inputs and defaults.** Trusted proxy CIDRs refer to real socket peers;
untrusted forwarded certificate markers cannot establish identity. Backend
certificate/key files and stored certificate IDs/thumbprints select real mTLS
transports. Stored certificates may reference signed local vault PKCS#12
secrets; fetched bundles are bounded and loaded through mode0600 temporary PEM
files removed immediately after SSLContext loading. Changes to credential files
or vault bundle digests create a new connection pool. Retired pools close after
active response streams finish, and the reusable cache is bounded. Callout
certificate selections do not alter the main backend's certificate. File-based
certificate selection does not implement inline PKCS#12 policy bodies.

**Order and result.** Certificate checks run before inbound execution and
policy checks run in inbound order. Workload identity defaults to signed RS256
credentials with issuer/audience/identity/lifetime validation and explicit
resource grants. Local vault requests support signed identity and mTLS; gateway
requests snapshot secret resolutions, then subsequent requests see rotations.
OIDC JWKS and vault requests share the outbound firewall with backend/callout
traffic. Cipher protocol defaults require TLS 1.2 or newer.

**Local adaptations.** CA/CRL files and a signed local issuer reproduce the
security outcomes without requiring external services. They are local trust
infrastructure rather than Microsoft Entra credentials or an Azure certificate
store. Teaching examples must explicitly enable simulated certificate headers,
backend marker authentication, opaque workload tokens, or supplied forwarding
headers. Those adaptations default to disabled. See
[network and TLS details](security/network-tls.md) for settings and boundaries.

**Evidence.** [authentication-certificate](https://learn.microsoft.com/en-us/azure/api-management/authentication-certificate-policy),
[managed identity](https://learn.microsoft.com/en-us/azure/api-management/authentication-managed-identity-policy),
[validate-client-certificate](https://learn.microsoft.com/en-us/azure/api-management/validate-client-certificate-policy),
and [custom CA certificates](https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-ca-certificates).
`tests/test_network_certificate_security.py` verifies real TLS handshakes,
negative trust/expiry/revocation/protocol cases, signed JWT rejection, private
socket peers, firewall bypass attempts, and credential/secret rotation.
Confidence: `verified-subset` for local trust outcomes; cloud issuer/provisioning
steps use the explicit local adapters above.

## Cache, throttling, backend pools, and AI policies — adapted contracts

**Cache.** `cache-lookup`, `cache-store`, and value-cache policies use a local
in-memory cache. GET eligibility, authorization/private-response rules,
duration, key, default value, and policy order follow the documented subset.
`prefer-external` falls back to local cache; `external` is unsupported; local
writes are synchronous. A local cache is volatile and single-process, so shared
regional/distributed cache behavior is not promised.

**Throttling and quotas — supported subset.** `rate-limit` and
`rate-limit-by-key` support subscription/key counters with the documented
status families and headers. `throttling.algorithm` selects `sliding-window`
(the default, for classic behavior) or `token-bucket` (for v2 behavior).
Token buckets start full, refill continuously at `calls / renewal-period`,
and cap their available tokens at `calls`. The guide's six-call, sixty-second
bucket admits one additional call after ten seconds. `quota-by-key` supports
call limits, bandwidth limits, or both, with response-conditioned increments
and renewal. Repeated policies sharing a key count each request once.
Bandwidth accounting includes request and response bodies after the response
is known. Rounding up to kilobytes is a local choice where the reference does
not specify rounding; exhaustion rejects the next request
with 403 and Retry-After. Counters are local to one gateway process;
distributed counter drift and cross-gateway synchronization are not claimed.
Evidence: `tests/test_advanced_throttling_guide.py`,
`tests/test_policy_throttling.py`, the
[advanced throttling guide](https://learn.microsoft.com/en-us/azure/api-management/api-management-sample-flexible-throttling),
and [quota-by-key reference](https://learn.microsoft.com/en-us/azure/api-management/quota-by-key-policy).
Confidence: `verified-subset` for these local algorithms and limits.

**Backend pools.** Pool backends support local priority/weight selection,
failover, session-affinity markers, and per-member adapted circuit breakers.
Azure's distributed backend and breaker timing/selection behavior is not
claimed; cookie details not defined by Learn are local.

**AI policies.** `llm-token-limit` and `llm-emit-token-metric` are local adapted
policies. They parse supported OpenAI/Anthropic/Vertex usage shapes, count local
streaming responses, and emit OTEL counters. Semantic cache and content safety
remain unsupported in this repository and are delegated to the sibling AI
Foundry simulator integration. Tokenization, remaining-quota precision, and
metric backend behavior do not claim Azure identity.

**Evidence.** [cache lookup](https://learn.microsoft.com/en-us/azure/api-management/cache-lookup-policy),
[caching overview](https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-cache),
[rate limit](https://learn.microsoft.com/en-us/azure/api-management/rate-limit-policy),
[advanced throttling](https://learn.microsoft.com/en-us/azure/api-management/api-management-sample-flexible-throttling),
[backends](https://learn.microsoft.com/en-us/azure/api-management/backends), and
[AI gateway policies](AI-GATEWAY.md). Confidence: `adapted`.

## Management and observability — local control-plane contract

**Contract.** Tenant-key-protected local CRUD, inspection, replay, traces, and
OTEL integration support development workflows. Resource IDs and JSON shapes
are APIM-shaped projections, not ARM or SDK wire compatibility.

**Inputs and defaults.** Management is available only when
`tenant_access.enabled` is true. Writes synchronously update local config and
reload the gateway. Trace lookup uses the local trace ID and optional trace
headers. Logger and diagnostic resources preserve descriptive settings.

**Order and result.** Management writes are validated and normally reflected in
subsequent gateway requests. Persistence and reload are local best-effort
operations: the current implementation does not promise an atomic transaction
or rollback if a file write/reload fails. Replay executes through the same local
gateway pipeline. Trace summaries expose policy steps, routing, selected
backend, cache/throttle actions, and forwarded-header fields where enabled.

**Errors and exclusions.** ARM authentication, asynchronous ARM operation
semantics, ETags/concurrency, Azure RBAC, Application Insights ingestion, and
full diagnostic logger routing are unsupported. OTEL/Grafana and local traces
are the observability implementation; simulator-only response/correlation
headers are opt-in adaptations.

**Evidence.** [set/edit policies](https://learn.microsoft.com/en-us/azure/api-management/set-edit-policies),
[APIM tracing](https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-api-inspect),
`docs/APIM-SDK-SURFACE-GUIDE.md`, and the management/trace owner tests in
`contract_matrix.yml`. Confidence: `adapted`.

## Local tutorial services

API-scoped debug credentials authorize tracing for at most one hour using
`Apim-Debug-Authorization`; trace lookup uses `Apim-Trace-Id`. Local monitoring
provides metrics, management activity logs, sampled gateway resource logs, and
Fired/Resolved alert events with local action groups. Observations and credentials
are in-memory; settings and rules persist. These additions do not consume request
bodies or change policy results.

The developer portal maintains independent draft/published branding, theme, pages
and uploaded images. Protected preview sees drafts; anonymous consumers see only
published content. The portal groups API versions and generates Segment, Header,
or Query selectors. Product publication controls discovery; terms and limits apply
to consumer signup. Management-created products default to unpublished.

API Center maintains a persisted one-way local catalog of all source APIs,
including optional exported OpenAPI. Create/edit/delete operations synchronize
immediately; unlink removes synchronized assets. One link is allowed per simulator.

Evidence: [local tutorial guides](tutorials/apim-get-started/README.md),
`tests/test_debug_credentials.py`, `tests/test_local_monitoring.py`,
`tests/test_portal_customization.py`, `tests/test_local_api_center.py`,
`tests/test_product_authoring.py`, and `tests/test_version_workflow.py`.

## Evidence maintenance rule

When adding a supported contract, add one focused owner test and one Microsoft
Learn link. When behavior is intentionally adapted, record the adaptation in
the contract and matrix before changing the implementation. When a reference
is silent, label the local choice as an inference instead of presenting it as
Azure behavior.

## Policy guide adapters — local contracts

The thirteen policy guide journeys live in `examples/apim-policies`. Their
[dated inventory](policy-validation/INVENTORY.md) records source hashes,
fixtures and explicit adaptations. Validation proves the listed outcomes,
not every policy reference or every supported .NET expression.

**Messaging and logging.** A separate Compose HTTP pub/sub container implements
queue FIFO, independent topic subscriptions, TTL and sender permissions.
`send-service-bus-message` maps namespace/identity to an explicit local binding;
`log-to-eventhub` maps logger destinations to broker topics. Logging's local
`ToHttpMessage` helper preserves bodies and filters credentials. It replaces
C# LINQ serialization used by the source guide. `send-one-way-request` schedules
a background HTTP notification without changing the original response on a
transport failure. AMQP, Azure delivery infrastructure and Moesif analytics
are outside this adapter's contract.

**Secrets.** Named value display-name changes update references. An explicitly
configured local vault base maps secret identifiers to a read-only HTTP secret
endpoint, with no external fallback. Secrets are refreshed for subsequent
requests. Environment overrides remain available. This simulates the guide's
secret rotation workflow without cloud identity or Key Vault dependencies.

**GraphQL.** Schema-driven execution supports HTTP data-source field resolvers,
arguments, nested parent values and resolver request/response policies. Local
management APIs author schemas and resolver mappings. This guide's HTTP resolver
workflow does not establish Cosmos DB or Azure SQL resolver support.

**Authoring and debugging.** Copilot and VS Code guides are represented by
local management authoring, execution of the documented prompt outcomes,
trace variables/steps and replay. No Azure CLI emulation or editor extension
is involved.
