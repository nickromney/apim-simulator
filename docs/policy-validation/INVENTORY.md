# Policy guide validation inventory — 2026-10-02

This inventory covers the **13 guides requested for local simulation**. It does
not recursively audit every entry in the policy reference. Local authoring,
gateway execution, trace inspection, messaging, and resolver outcomes are the
validation targets. Azure deployment, SDK, Copilot, and editor integration are
not substitutes for those outcomes.

[The machine-readable inventory](inventory.json) records the official guide
URLs, source dates and SHA256 hashes, each fenced example's location and hash,
parser registration observed at baseline `be578e3`, and relevant existing test
candidates. Microsoft publishes its example code under
[MIT](https://github.com/MicrosoftDocs/azure-docs/blob/main/LICENSE-CODE);
Microsoft and contributors retain the attribution for the extracted XML in
[the fixture directory](../../tests/fixtures/policy_guides/README.md).

## Source inventory and evidence rules

The 13 sources contain 48 fenced code blocks, including 33 XML specimens. The
fixtures preserve policy code; source private addresses are adapted to
loopback, and concrete source webhook/application endpoints are replaced by
reserved placeholders, with explicit records. Documentation prose and screenshots are not
copied. Copilot and VS Code examples are prompts and screenshots, so their
locally authored policies must be identified as prompt outcomes rather than
unchanged source XML.

Fifteen XML specimens require markup repair before parsing as fragments. The
external-service examples include unescaped quotation marks inside XML
attributes, unclosed `set-variable` tags, and unescaped URL ampersands. Some other
blocks are deliberately incomplete templates or include C# generic type syntax
inside XML text that requires escaping. Inventory entries record these
as `source_markup_requires_repair`; that is source-shape evidence, not a gateway
failure. A runnable adaptation must record its wrapper, markup repair, and mock
endpoint replacement separately.

`registered` records parser dispatch only. A policy name used as a child, such
as `set-method` inside `send-request` or `set-status` inside `return-response`,
is not equivalent to a standalone policy registration. Existing test candidates
are file mappings, not a claim that their tests passed. Runtime and host-port
results must remain separate from source extraction and parser inspection.
`not_run` is retained until a concrete execution result is recorded.

## Thirteen guide outcomes

| # | Official guide | Local outcome to validate | Evidence and baseline gap |
| --- | --- | --- | --- |
| 1 | [Policy overview](https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-policies) | XML pipeline order; `<base/>` placement and suppression; product context; source `cross-domain` / `find-and-replace` example; user and region header values | `test_policy_scopes.py`, `test_effective_policy.py`; baseline rejected both source transform elements. New transform regressions are in `test_policy_transforms.py`; `test_policy_guide_expressions.py` verifies user/region values and the live context runner repeats them. |
| 2 | [Set or edit policies](https://learn.microsoft.com/en-us/azure/api-management/set-edit-policies) | Save/read/edit policy at a scope; immediate effect; reject invalid XML without losing the saved policy; effective-policy order; IP allow/deny | `test_policy_guide_authoring.py`, `test_policy_inspection.py`, `test_policy_access.py`. Local management calls exercise the editor's underlying outcomes. |
| 3 | [Author policies using Azure Copilot](https://learn.microsoft.com/en-us/azure/copilot/author-api-management-policies) | Author five-calls-per-second limit, remove response version header, and role-dependent response policy; inspect and refine authored policy | `test_policy_guide_authoring.py` covers the rate/header prompts; `test_policy_guide_expressions.py` and `expressions_graphql.role_filters` cover JWT-validated member/admin filtering and token rejection. The guide supplies no deterministic generated XML. |
| 4 | [Create and debug policies in VS Code](https://learn.microsoft.com/en-us/azure/api-management/api-management-debug-policies) | Author a backend URL plus request header; author a 100-calls-per-minute policy; inspect operation effective policies, variable writes and policy execution, errors, and body-preserving replay | `test_policy_guide_authoring.py`, `test_debug_credentials.py`. The local debug transport uses API-scoped credentials and stored trace/replay, with explicit evidence required for the guide's interactive debugger outcomes. |
| 5 | [Policy expressions](https://learn.microsoft.com/en-us/azure/api-management/api-management-policy-expressions) | Single expression, conversion, string length, named regex capture and null access, variable fallback, header-array `TryGetValue`, base64 decoding, returning code block | `test_policy_guide_expressions.py` executes the six initial source expressions individually and their present/absent branches. The reference's entire allowed .NET type table is not implied by passing these examples. |
| 6 | [Policy fragments](https://learn.microsoft.com/en-us/azure/api-management/policy-fragments) | Create/update/include reusable fragment; immediate effect at every referencing scope; effective view expansion; dependency/deletion and invalid/nested fragment checks | `test_policy_structure.py`, `test_policy_inspection.py`, and `test_policy_guide_expressions.py` validate source user/region headers, updates shared by two APIs, expanded effective view, and protected deletion. |
| 7 | [Error handling](https://learn.microsoft.com/en-us/azure/api-management/api-management-error-handling-policies) | Skip remaining normal pipeline on error; run `on-error`; project LastError properties and nested path/policy ID; source customized headers/status/body | `test_policy_on_error.py`; the guide lists standalone `set-status` among permitted error handlers; baseline lacked it. The exact LastError-header example and standalone status/body transforms now execute in `test_policy_guide_expressions.py` and `test_policy_transforms.py`. |
| 8 | [Advanced logging](https://learn.microsoft.com/en-us/azure/api-management/api-management-log-to-eventhub-sample) | Request/response application-HTTP event payloads, headers, URL/status, correlation and body preservation; partitioned logger delivery and downstream observation | Baseline lacked `log-to-eventhub`. `test_local_pubsub.py` and `verify.logging` now exercise local event delivery, source-shaped HTTP/correlation expressions, partitions, credential filtering and body preservation. Individual unasserted specimens remain `not_run`. |
| 9 | [Advanced request throttling](https://learn.microsoft.com/en-us/azure/api-management/api-management-sample-flexible-throttling) | Subscription limit and quota; IP, JWT subject and client-supplied key isolation; increments conditioned on response; response headers; renewal | `test_policy_throttling.py`; exact source snippets in the fixture directory. New source-backed cases belong in `test_advanced_throttling_guide.py`. Distributed regions are modeled as local counters rather than silently claimed as Azure synchronization. |
| 10 | [Using external services](https://learn.microsoft.com/en-us/azure/api-management/api-management-sample-send-request) | Conditional Slack-shaped callout to a local receiver; reference-token introspection; inactive/null response failure; four-request composition with query parameters and preserved callout bodies | `test_policy_callouts_fidelity.py`, `test_external_composition_guide.py`, `verify.external` and `external_composition.run` verify callouts, denied null introspection and four-source aggregation. Recorded markup repairs preserve source expressions. |
| 11 | [Send messages to Service Bus](https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-send-service-bus) | Send incoming body through a configured local managed-identity sender to a queue/topic, and observe the message without an ordinary backend | Baseline lacked `send-service-bus-message`. `test_local_pubsub.py` and `verify.messaging` now assert queue/topic fanout, independent consumers, payload/properties/TTL, sender permissions, and immediate 201 without backend forwarding. |
| 12 | [Named values](https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-properties) | Plain/secret/expression values; placeholders in headers/names/combined values; updates propagate; inspection masks secrets; dependency checks; local secret source resolves values | `test_policy_guide_expressions.py` executes source specimens 06/08/09; `test_named_value_guide_lifecycle.py` and the live runner verify updates, renaming, masking and local vault rotation. |
| 13 | [Configure a GraphQL resolver](https://learn.microsoft.com/en-us/azure/api-management/configure-graphql-resolver) | Field-bound resolver CRUD and invocation; request/optional response transform; parent/argument context; multiple fields; resolver scope independence | Baseline lacked a synthetic resolver runtime. `test_graphql_resolvers.py` and the live runner now execute source Parent/Arguments requests with local HTTP data retrieval, variables, projection and invalid-query rejection. |

## Local authoring and transform regression checks

The new authoring cases use a tenant-protected local management API and a
controlled HTTP backend. They save, read and edit policy; compare effective
policy order to gateway results; verify a malformed save preserves the previous
policy; delete the authored API; execute the rate/header prompt results; route
to the authored backend; inspect scoped debug traces and variable writes; and
replay a request while asserting identical body and header results.

The source overview transform fixture is exercised with a parent replacement
before the child replacement, making the `<base/>` ordering observable in the
upstream body. An authored outbound replacement then verifies response
buffering and body preservation under scoped debug tracing. Additional cases
cover attribute expressions, empty replacement, every permitted section,
invalid XML shapes, and the Adobe policy response.

Focused local verification passed 55 tests across authoring, transforms, scopes,
effective policies, inspection, and debug credentials. Ruff lint and formatting
checks passed for the four authored Python files. The runner itself also has a
local gateway regression that checks global-policy restoration and API cleanup.
Its published-port execution is recorded separately by the aggregate lab.

Execution results are recorded in `inventory.json` as they become available.
A guide is not marked complete merely because its examples were extracted,
its policies parse, or neighboring tests passed. The final cross-guide report
must list any remaining outcome without local execution evidence.

## Published local gateway evidence

The aggregate runner at `examples/apim-policies/verify.py` passed twice through the
published gateway and broker ports `localhost:8900` and `localhost:8901`.
Its assertions cover messaging fanout/queue delivery, correlated application
HTTP events, reference-token grants/denials and one-way alerts, six throttling
journeys, four authoring/debugging journeys, and five expression/context/resolver
journeys. The durable [live execution record](live-results-2026-10-02.json)
contains both runs and their asserted outcomes.
The companion [context and resolver findings](scopes-auth-findings.md) and
[throttling findings](throttling-findings.md) map their source-backed cases.
The final Python suite passed 927 tests with one optional Keycloak integration
test skipped. The focused authoring, transforms, composition, expressions,
error, effective-policy and inspection check passed all 86 tests.

The authoring runner now exercises the repaired source IP filter, while the
context runner authors the role-dependent Copilot prompt with JWT validation
before member/admin response filtering. Prompt outcomes are explicitly authored
local policies. Stored traces and replay provide the local debugging workflow;
the report does not claim a connection to the Azure authoring/editor services.

Source specimen status is independent of guide workflow status. The inventory
marks only fixtures asserted by concrete tests as executed. Empty scaffolding,
illustrative fragments, and reference tables remain `not_run`. Passing the six
initial expression examples does not imply the entire documented .NET type
table is implemented.

The send-request guide's dashboard composition is tracked explicitly. Its source
passes `fromDate` to both backend parameters, and its complete XML includes
unclosed variable tags and stray quotation marks. The local adaptation repairs
markup and replaces hosts, checks the original repeated-date behavior, then
refines the authored policy to pass `toDate`. Its four callout bodies must
remain available under tracing, and a null response from a refused introspection
connection denies access with 401 and a Bearer challenge. Both published-port
runs now confirm these outcomes. XML wrappers and syntactic repairs are recorded
separately from endpoint substitutions and the deliberate `toDate` refinement.
