# Next Features

This document is the internal implementation spec for the next phase of the APIM simulator. It extends, rather than replaces, [SCOPE.md](./SCOPE.md) and [CAPABILITY-MATRIX.md](./CAPABILITY-MATRIX.md).

The current baseline already includes:

- the standalone Python/FastAPI gateway
- the OIDC example stack
- the MCP example stack
- the existing smoke scripts
- the current APIM policy subset

The next phase is deliberately sequenced:

1. Networking overlays and edge-behavior simulation
2. APIM sample compatibility and policy-surface expansion
3. Developer console UI

## Delivery Order

The simulator remains a Docker Compose-first data-plane lab. This phase does not expand into Kubernetes deployment guidance or broad management-plane parity.

### Milestone 1: Networking First

The first milestone is a compose-first edge simulation layer around the existing MCP path. The goal is to make the repo capable of exercising the same gateway logic in three shapes:

- direct public APIM
- APIM behind an edge proxy
- APIM reachable only from an internal network

The MCP HTTP example remains the primary validation path for this milestone.

### Milestone 2: Policy Compatibility

The second milestone expands the simulator toward a better APIM lab subset, not full Azure APIM parity. The outcome is a curated compatibility harness around selected `Azure-Samples/Apim-Samples` fixtures and a larger practical policy surface.

### Milestone 3: Developer Console

The third milestone adds a thin operational UI over the existing simulator. It is not a developer portal and does not attempt to reproduce the APIM CMS, sign-up, publishing, or notification workflows.

## Milestone 1: Networking Overlays

### Compose Refactor

Refactor the local runtime so `compose.yml` becomes the internal-only base stack. It should define services, networks, and dependencies, but should not publish APIM or upstream ports to the host by default.

Add the following overlays:

- `compose.public.yml`
  - restores the current direct host-facing APIM path on `localhost:8000`
  - keeps the current `make up` and `make up-mcp` experience intact by making those targets use the public overlay
- `compose.edge.yml`
  - adds an `edge-proxy` service in front of the simulator
  - uses `nginx` as the reverse-proxy implementation
  - exposes HTTP on `apim.localtest.me:8088`
  - sets and forwards `Host`, `X-Forwarded-For`, `X-Forwarded-Host`, and `X-Forwarded-Proto`
- `compose.tls.yml`
  - layers on top of `compose.edge.yml`
  - terminates TLS at the edge proxy on `https://apim.localtest.me:8443`
  - exposes an HTTP listener on `:8080` that redirects to `:8443`
- `compose.private.yml`
  - keeps the stack internal-only
  - adds a one-shot `smoke-runner` or equivalent probe container on the compose network so private reachability can be tested without publishing host ports

The resulting scenario matrix is:

| Scenario | Compose files | Entry path | Purpose |
| --- | --- | --- | --- |
| Direct public | `compose.yml` + `compose.public.yml` + `compose.mcp.yml` | `http://localhost:8000/mcp` | Baseline local APIM path |
| Edge HTTP | `compose.yml` + `compose.edge.yml` + `compose.mcp.yml` | `http://apim.localtest.me:8088/mcp` | Reverse proxy and forwarded-header behavior |
| Edge TLS | `compose.yml` + `compose.edge.yml` + `compose.tls.yml` + `compose.mcp.yml` | `https://apim.localtest.me:8443/mcp` | TLS termination plus forwarded-header behavior |
| Private | `compose.yml` + `compose.private.yml` + `compose.mcp.yml` | no host entrypoint | Internal-only simulation |

### Supporting Files

Add the following implementation assets:

- `examples/edge/nginx.conf`
  - canonical reverse-proxy config shared by edge and TLS scenarios
- `scripts/gen_dev_certs.sh`
  - generates local development certificates using `openssl`
  - no dependency on `mkcert`
- `scripts/smoke_edge.py`
  - verifies forwarded-header behavior and end-to-end MCP access through the edge proxy
- `scripts/smoke_private.py`
  - verifies that host access is unavailable while internal compose-network access still succeeds

### Make Targets

Add these user-facing targets:

- `make up-tls`
- `make up-edge`
- `make smoke-edge`
- `make smoke-private`

Also add matching compose rendering helpers:

- `make compose-config-edge`
- `make compose-config-tls`
- `make compose-config-private`

Keep `make up`, `make up-mcp`, and `make up-oidc` working after the compose refactor.

### Header and Trace Behavior

Networking work must preserve and prove:

- incoming `Host`
- `X-Forwarded-For`
- `X-Forwarded-Host`
- `X-Forwarded-Proto`

Extend trace payloads to capture:

- `incoming_host`
- `forwarded_host`
- `forwarded_proto`
- `forwarded_for`
- `client_ip`
- `upstream_url`

Do not introduce broad edge-product emulation. This milestone is only for reachability, TLS termination, host matching, and forwarded-header behavior. WAF, health-probe management, autoscaling, and true Azure Application Gateway parity are out of scope.

## Milestone 2: Policy Compatibility and Azure Sample Coverage

### Compatibility Harness

Create a curated fixture harness under `tests/fixtures/apim_samples/`. The simulator should not claim full compatibility with the upstream sample repo. Instead, it should classify and test a known subset.

Use this structure:

- `tests/fixtures/apim_samples/manifest.json`
  - list of curated fixtures
  - fields: `id`, `source`, `status`, `notes`
  - `status` must be one of `supported`, `adapted`, `unsupported`
- `tests/fixtures/apim_samples/<fixture-id>/policy.xml`
- `tests/fixtures/apim_samples/<fixture-id>/request.json`
- `tests/fixtures/apim_samples/<fixture-id>/expected.json`

Add a compatibility runner and Make target:

- `scripts/check_sample_compat.py`
- `make compat`

`make compat` must:

- execute all curated fixtures
- fail if a `supported` fixture regresses
- fail if an `adapted` fixture no longer matches its documented adapted behavior
- print a summary of `unsupported` fixtures in CI output without treating them as failures

### Policy Engine Expansion

Implement the first additional policy primitives in this order:

1. `set-variable`
2. `set-query-parameter`
3. `set-body`
4. `include-fragment`

These additions must extend the current deliberately narrow simulator model. Do not add the Azure APIM C# expression engine.

Use one shared value-rendering helper for policy values and URI/body/query mutations. The helper should support:

- literals
- existing request method and path
- request headers
- request query parameters
- request-scoped variables
- subscription ID when present

`set-variable` should write into the existing per-request `variables` map used by the policy engine.

`set-query-parameter` should mutate the outbound upstream query string only.

`set-body` should support literal or templated body replacement for simulator-controlled requests and short-circuit responses. It does not need to support streaming transformations in this phase.

`include-fragment` should be backed by an additive root config field:

- `policy_fragments: dict[str, str]`

Fragments are resolved by name from config and inserted into the parsed policy tree. File-system fragment discovery is out of scope for this phase.

### Explicitly Deferred

The following remain deferred after this milestone:

- `send-request`
- `cache-*`
- managed identity emulation beyond the current header-level placeholder behavior
- broad control-plane emulation
- full policy expression compatibility with Azure APIM

Compatibility is measured by documented simulator behavior and tests, not by claiming drop-in Azure equivalence.

## Milestone 3: Developer Console

### Product Shape

Build a thin operational console, not a portal. Its first slice is a local operator UI for inspecting and editing the simulator state that developers already manage by JSON and trace APIs today.

Use:

- `ui/` for a Vite + React + TypeScript frontend
- a separate compose service for the UI when this milestone starts

### First UI Scope

The first console slice must support:

- viewing APIs, routes, products, and subscriptions
- viewing and editing policy text
- replaying requests through the gateway
- viewing trace output
- rotating or inspecting subscription keys

The following remain out of scope:

- self-service user sign-up
- CMS-like documentation pages
- documentation publishing
- email templates
- notifications
- a public developer portal experience

### Required Admin API Surface

Additive admin endpoints under `/apim/management` must support the console. The minimum required endpoints are:

- `GET /apim/management/summary`
  - returns APIs, routes, products, subscriptions, and backend summaries
- `GET /apim/management/policies/{scope_type}/{scope_name}`
  - fetches the effective raw policy XML for gateway, API, or operation scope
- `PUT /apim/management/policies/{scope_type}/{scope_name}`
  - updates policy XML in the loaded config and triggers the existing reload flow
- `GET /apim/management/traces`
  - returns recent trace summaries
- `POST /apim/management/replay`
  - executes a replayable request through the gateway and returns response plus trace metadata

Existing subscription rotation endpoints remain the mechanism for key rotation.

## Public Interfaces and Contract Changes

The following external changes are part of this phase:

- Compose overlays become supported runtime entrypoints rather than ad hoc local experiments.
- Make targets become the primary user UX for running direct, edge, TLS, and private scenarios.
- Gateway config changes remain additive only.
- The only new planned root config field in this phase is `policy_fragments`.
- Networking-related config changes should stay narrowly focused on external host and proxy awareness.
- Python remains the gateway/runtime implementation language for this phase.
- No Go, Rust, Zig, or Erlang rewrite is part of the planned work.
- Existing OIDC and MCP examples must continue to run without Azurite or the Service Bus emulator.

## Acceptance Criteria

### Networking

- The direct public compose path works end to end.
- The edge-proxy compose path works end to end.
- The TLS edge path works end to end with repo-generated development certificates.
- The private/internal mode exposes no host-facing APIM entrypoint.
- Private-mode smoke verification succeeds from the internal probe container.
- Forwarded-header behavior is visible in traces and preserved to the upstream echo/MCP target.
- MCP requests succeed through direct, edge, TLS, and private scenarios.

### Policy Compatibility

- Unit tests exist for each newly supported policy primitive.
- Curated APIM sample fixtures execute through the compatibility harness.
- Supported and adapted fixtures are enforced in CI.
- Unsupported fixtures are reported clearly in CI output.
- Existing policy behavior remains unchanged unless explicitly covered by new fixture expectations.

### Regression

- `uv run --extra dev pytest -q` remains green.
- The current OIDC smoke flow remains green.
- The current MCP smoke flow remains green.
- The new networking smoke flows remain green.

## Assumptions and Defaults

- Docker Compose remains the only deployment target in this phase.
- Kubernetes work is intentionally deferred.
- The simulator remains a data-plane learning tool rather than a full APIM management-plane reimplementation.
- Azurite and the Service Bus emulator remain optional scenario add-ons and do not block the networking or compatibility milestones.
- The current narrow policy grammar remains the foundation for future policy work.
