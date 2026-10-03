# Full APIM documentation assessment, 2026-10-03

The [Microsoft APIM documentation tree](https://learn.microsoft.com/en-us/azure/api-management/)
contains 320 table-of-contents entries in the retrieved snapshot. Every entry is
assessed in [the inventory](inventory-2026-10-03.jsonl), including repeated links
in different sections. All 308 entries linking to Microsoft Learn were retrieved;
their article-text hashes are recorded. The other twelve link to external tools,
registries, community sites or resources. Retrieving a page is not evidence that
its complete workflow works. Detailed source review targeted implementation gaps;
coverage judgments also use repository code, contracts and existing validation.

The starting assessment counts are **118 covered, 81 partial, 70 candidates and
51 reference entries**. They are a feasibility triage, not 320 acceptance tests.
"Covered" means the named local equivalent or documented subset exists; it does
not promise every option on the linked page. "Partial" identifies an existing
capability with a material gap. "Candidate" is potential work, not implemented
support. References include commercial tier information, historical migrations,
SDKs and conceptual material. Feature-level contracts remain authoritative in
[FIDELITY-CONTRACTS.md](../FIDELITY-CONTRACTS.md).

## Priorities and boundaries

| Priority | Addition | Why it is useful locally | Verification needed |
| --- | --- | --- | --- |
| First batch | Postman collection export; `set-method` policy | Small client interoperability and request-transform gaps | Scoped authorization, secret-free portable export; actual backend method/body and expression behavior |
| Next | XML `retry`, keyed `limit-concurrency`, parallel `wait` | Exercise resilience and fan-out inside authored policies | Failure/retry timing, termination, cancellation, shared limits and body replay |
| Next | Shared Redis cache | Compare cache behavior across gateway replicas | Cross-replica hits, TTL, region/default selection, cache outages and credential handling |
| Next | Local APIOps extract/diff/publish | Review configuration in Git and apply atomically | Secret references, round trips, dry-run, conflict detection and failed-publish preservation |
| Next | REST-to-MCP tools and GraphQL request validation | Real protocol adaptation and query safety | Tool discovery/invocation/auth; schema, depth/size limits and operation authorization |
| Larger | Workspace ownership and inheritance | Isolate teams' APIs/resources rather than only map identity grants | Cross-workspace denial, policy inheritance and gateway assignments |
| Larger | Gateway registration/config polling; lifecycle CloudEvents | Exercise disconnected gateways and automation | Signed snapshots, heartbeat, last-known-good recovery, event delivery/retry |
| Later | OData import, portal deployment/email lifecycle, WebSockets | Useful authoring and consumer journeys | EDMX fixtures, portable portal snapshots, outbox/templates, real protocol handshake/disconnect |

Other candidates in the inventory include OAuth credential management, model
aliases, A2A import, Dapr integrations and content-safety/semantic-cache clients.
These need bounded contracts and independent backing services where applicable.
Avoid rebuilding services already provided by the sibling Foundry simulator.

Local simulation remains the goal: security, identity, networking, messaging,
recovery and deployment outcomes get real local equivalents. Provisioning tools
can use Docker, UI/API and Bruno. Azure CLI emulation, editor extensions, cloud
billing and commercial SLAs are not useful implementation targets. gRPC and
WebSockets require actual transports; an HTTP-shaped mock is not protocol support.

## First implementation batch

Postman export and `set-method` are implemented from their primary
references: [Postman export](https://learn.microsoft.com/en-us/azure/api-management/export-api-postman)
and [set-method](https://learn.microsoft.com/en-us/azure/api-management/set-method-policy).
Their supported subsets are documented in [Postman export](../POSTMAN-EXPORT.md)
and [set-method](../SET-METHOD.md), with owner tests in the contract matrix.
The inventory records the state before this batch.

The final full regression suite passed 1,135 tests with one optional Keycloak
integration skipped. All 20 focused export tests pass, including the short-key
redaction regression. Python lint/format, Markdown and YAML
checks pass. Collections for unversioned, path-, query- and header-versioned APIs
validate against the official Postman v2.1 schema.

[Three real localhost TCP checks](live-results-2026-10-03.json) verify method/body
forwarding with outbound expression context, unauthorized export denial and a
schema-valid authorized collection retaining its query discriminator. A Docker
image smoke was attempted but the Docker daemon was stopped; native uvicorn and
an isolated HTTP backend provided actual socket verification instead. The
temporary servers were shut down after verification. Docker packaging for this
new batch remains unverified.

The preceding policy/security implementation is committed as `153bb6f`. Its
large diff includes dependency lockfile upgrades, executable labs, tests and
dated evidence; no runtime keys, certificates or databases were committed.
