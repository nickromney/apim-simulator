# NEXT-FEATURES Acceptance

Audited on `2026-04-02` against [NEXT-FEATURES.md](./NEXT-FEATURES.md).

## Summary

- Overall closure status: `met`
- Remaining `blocked` items: none
- Remaining `needs-fix` items: none after this closure pass

## Status Legend

| Status | Meaning |
| --- | --- |
| `met` | Implemented and evidenced locally in this branch |
| `needs-fix` | Spec divergence that still requires code or doc correction |
| `blocked` | Cannot be claimed complete because required repo-level support is missing |

## Milestone 1: Networking Overlays

| Spec item | Current implementation | Evidence | Status |
| --- | --- | --- | --- |
| `compose.yml` becomes the internal-only base stack with no host-facing APIM or upstream ports | Base stack uses `expose` only for `apim-simulator:8000` and `mock-backend:8080` | `compose.yml` inspection on `2026-04-02` showed `expose` without `ports` | `met` |
| `compose.public.yml` restores direct host-facing APIM on `localhost:8000` and keeps `make up` / `make up-mcp` intact | Public overlay publishes `8000:8000`; `make up` and `make up-mcp` use public overlay | `compose.public.yml` inspection; `make compose-config`; `make up` and `make up-mcp` both succeeded on `2026-04-02` | `met` |
| `compose.edge.yml` adds nginx `edge-proxy` on `apim.localtest.me:8088` and forwards `Host` / `X-Forwarded-*` | Nginx edge overlay publishes `8088`; shared config forwards `Host`, `X-Forwarded-For`, `X-Forwarded-Host`, `X-Forwarded-Proto` | `compose.edge.yml` and `examples/edge/nginx.conf` inspection; `make up-edge && make smoke-edge` passed on `2026-04-02` | `met` |
| `compose.tls.yml` layers on edge mode, serves TLS on `:8443`, and redirects `:8080` to TLS | TLS overlay publishes `8080` and `8443`; nginx config redirects `8080` to `https://apim.localtest.me:8443` | `compose.tls.yml` and `examples/edge/nginx.conf` inspection; `make up-tls && make smoke-tls` passed on `2026-04-02` | `met` |
| `compose.private.yml` keeps the stack internal-only and adds a one-shot probe container | Private overlay defines `smoke-runner` on the compose network and publishes no host port for APIM | `compose.private.yml` inspection; `make smoke-private` succeeded on `2026-04-02` after printing `Host port 8000 is unavailable, as expected.` | `met` |
| Scenario matrix exists for direct public, edge HTTP, edge TLS, and private paths | All four runtime shapes are available and exercised by repo commands | `make up` direct curl check, `make up-edge && make smoke-edge`, `make up-tls && make smoke-tls`, and private compose + `make smoke-private` all succeeded on `2026-04-02` | `met` |
| Supporting files: `examples/edge/nginx.conf`, `scripts/gen_dev_certs.sh`, `scripts/smoke_edge.py`, `scripts/smoke_private.py` | All four files exist and are active in the overlays/smokes | File inspection on `2026-04-02`; live edge/TLS/private runs used those files successfully | `met` |
| Make targets: `make up-tls`, `make up-edge`, `make smoke-edge`, `make smoke-private` | All targets are present and callable | `Makefile` inspection; each target ran successfully on `2026-04-02` | `met` |
| Compose rendering helpers: `make compose-config-edge`, `make compose-config-tls`, `make compose-config-private` | All render helpers exist and succeed | `make compose-config-edge`, `make compose-config-tls`, and `make compose-config-private` all exited `0` on `2026-04-02` | `met` |
| `make up`, `make up-mcp`, and `make up-oidc` continue to work after refactor | Existing entrypoints still start their intended stacks | `make up`, `make up-mcp`, and `make up-oidc` all succeeded on `2026-04-02` | `met` |
| Networking work preserves and proves incoming `Host` plus `X-Forwarded-*` | Edge smokes assert echoed upstream headers and trace fields | `make smoke-edge` and `make smoke-tls` printed forwarded host/proto values and passed on `2026-04-02` | `met` |
| Trace payloads capture `incoming_host`, `forwarded_host`, `forwarded_proto`, `forwarded_for`, `client_ip`, `upstream_url` | Trace store and trace headers include all required fields | `tests/test_gateway.py::test_trace_payload_captures_forwarded_headers`; edge/TLS smokes passed on `2026-04-02` | `met` |
| No broad edge-product emulation is introduced | Repo only adds nginx reverse-proxy/TLS overlays and forwarding behavior; no WAF/AppGW feature set | `examples/edge/nginx.conf`, compose overlays, and capability docs inspection on `2026-04-02` | `met` |

## Milestone 2: Policy Compatibility and Azure Sample Coverage

| Spec item | Current implementation | Evidence | Status |
| --- | --- | --- | --- |
| Curated fixture harness exists under `tests/fixtures/apim_samples/` | Manifest plus per-fixture `policy.xml`, `request.json`, and `expected.json` are present | Directory inspection and `tests/fixtures/apim_samples/manifest.json` on `2026-04-02` | `met` |
| Manifest fields are `id`, `source`, `status`, `notes`; statuses are `supported`, `adapted`, `unsupported` | Manifest entries follow the requested shape | `tests/fixtures/apim_samples/manifest.json` inspection on `2026-04-02` | `met` |
| Compatibility runner and `make compat` exist | Runner lives in `scripts/check_sample_compat.py`; Make target invokes it | File inspection; `make compat` succeeded on `2026-04-02` | `met` |
| `make compat` executes all curated fixtures | Runner walks the manifest and executes all non-unsupported entries | `make compat` on `2026-04-02` reported supported and adapted fixture passes for every listed executable fixture | `met` |
| `make compat` fails if a `supported` fixture regresses | Runner accumulates failures and exits nonzero when any supported/adapted fixture fails | `scripts/check_sample_compat.py` inspection plus passing run on `2026-04-02` | `met` |
| `make compat` fails if an `adapted` fixture no longer matches documented adapted behavior | Adapted fixtures are executed and routed through the same failure path as supported fixtures | `scripts/check_sample_compat.py` inspection plus passing run on `2026-04-02` | `met` |
| `make compat` prints unsupported fixture summaries in CI output without failing | GitHub Actions workflow runs `make compat`, preserving the command stdout in CI logs | `.github/workflows/ci.yml` added on `2026-04-02`; local `make compat` prints `Unsupported fixtures:` | `met` |
| Additional primitives: `set-variable`, `set-query-parameter`, `set-body`, `include-fragment` | All four primitives are implemented in the narrow XML engine | `app/policy.py` inspection; `tests/test_policy_golden.py` and `make compat` on `2026-04-02` | `met` |
| No Azure APIM C# expression engine is added | Rendering remains a narrow simulator helper; no APIM expression runtime exists | `app/policy.py` inspection on `2026-04-02` | `met` |
| One shared value-rendering helper supports literals, method/path, headers, query params, variables, and subscription ID | Shared token renderer is used by header, URI, body, query, and response shaping | `app/policy.py` inspection; `tests/test_policy_golden.py` passed on `2026-04-02` | `met` |
| `set-variable` writes into request-scoped `variables` | Primitive mutates the in-flight request `variables` map | `app/policy.py` inspection; supported fixture `set-variable-header` passed on `2026-04-02` | `met` |
| `set-query-parameter` mutates outbound upstream query only | Primitive updates `PolicyRequest.query`; compat fixture proves upstream query mutation | Supported fixture `set-query-parameter-from-path` passed on `2026-04-02` | `met` |
| `set-body` supports literal or templated body replacement for request/short-circuit flows | Primitive rewrites request body and `return-response` body rendering | `tests/test_policy_golden.py`; supported fixture `set-body-template` passed on `2026-04-02` | `met` |
| `include-fragment` is backed by additive root config field `policy_fragments: dict[str, str]` | Config model includes `policy_fragments`; parser resolves fragments from config | `app/config.py`, `app/policy.py`, and adapted fixture `include-fragment-config` on `2026-04-02` | `met` |
| Fragments are resolved from config, not filesystem discovery | Resolver reads fragment XML from config only | `app/policy.py` inspection and adapted fixture pass on `2026-04-02` | `met` |
| Deferred items remain deferred: external cache backends, `quota-by-key` bandwidth, broader managed identity emulation, broad control-plane emulation, full policy expressions | Deferred capabilities remain documented as such | `docs/CAPABILITY-MATRIX.md`, `docs/SCOPE.md`, and unsupported fixture `cache-external-unsupported` | `met` |
| Compatibility is measured by documented simulator behavior and tests, not APIM equivalence claims | Repo docs and harness explicitly classify supported/adapted/unsupported subsets | `tests/fixtures/apim_samples/manifest.json`, `docs/SCOPE.md`, and `docs/CAPABILITY-MATRIX.md` inspection on `2026-04-02` | `met` |

## Milestone 3: Developer Console

| Spec item | Current implementation | Evidence | Status |
| --- | --- | --- | --- |
| Build a thin operational console, not a portal | UI is a local operator console over management APIs; no portal/CMS implementation exists | `ui/src/App.tsx` inspection; `docs/CAPABILITY-MATRIX.md` updated on `2026-04-02` | `met` |
| Use `ui/` with Vite + React + TypeScript | `ui/` exists with Vite, React 19, and TypeScript | `ui/package.json` inspection; `npm run build` succeeded on `2026-04-02` | `met` |
| Use a separate compose service for the UI | `compose.ui.yml` defines dedicated `ui` service on `localhost:3007` | `compose.ui.yml` inspection; `make up-ui` succeeded on `2026-04-02` | `met` |
| UI supports viewing APIs, routes, products, and subscriptions | Console summary view exposes those entities | `ui/src/App.tsx` inspection; `make up-ui` plus management summary curl succeeded on `2026-04-02` | `met` |
| UI supports viewing and editing policy text | Console loads/saves policy XML through management policy endpoints | `ui/src/App.tsx`; `tests/test_gateway.py::test_management_policy_get_put_updates_route_policy_in_memory` | `met` |
| UI supports replaying requests through the gateway | Console posts to `/apim/management/replay` and renders response/trace data | `ui/src/App.tsx`; `tests/test_gateway.py::test_management_replay_returns_response_and_trace` | `met` |
| UI supports viewing trace output | Console loads recent traces and selected trace detail | `ui/src/App.tsx`; management traces endpoint present | `met` |
| UI supports rotating or inspecting subscription keys | Console lists subscription keys and calls rotate endpoint | `ui/src/App.tsx`; management subscription rotate tests passed | `met` |
| Out-of-scope portal/CMS/email/notification/public portal items remain out of scope | Repo still has no portal/CMS/email implementation | `docs/SCOPE.md` and `docs/CAPABILITY-MATRIX.md` inspection on `2026-04-02` | `met` |
| `GET /apim/management/summary` exists and returns APIs/routes/products/subscriptions/backends | Endpoint implemented and tested | `app/main.py`; `tests/test_gateway.py::test_management_summary_lists_routes_and_gateway_scope` | `met` |
| `GET /apim/management/policies/{scope_type}/{scope_name}` exists | Endpoint implemented and tested | `app/main.py`; management policy GET/PUT test passed on `2026-04-02` | `met` |
| `PUT /apim/management/policies/{scope_type}/{scope_name}` exists and updates loaded config | Endpoint implemented and tested | `app/main.py`; management policy GET/PUT test passed on `2026-04-02` | `met` |
| `GET /apim/management/traces` exists and returns recent trace summaries | Endpoint implemented and used by UI | `app/main.py`; `ui/src/App.tsx` inspection on `2026-04-02` | `met` |
| `POST /apim/management/replay` exists and returns response plus trace metadata | Endpoint implemented and tested | `app/main.py`; `tests/test_gateway.py::test_management_replay_returns_response_and_trace` | `met` |
| Existing subscription rotation endpoints remain the key-rotation mechanism | Rotation still occurs through management/admin subscription endpoints | `app/main.py`; subscription rotation tests passed on `2026-04-02` | `met` |

## Public Interfaces and Contract Changes

| Spec item | Current implementation | Evidence | Status |
| --- | --- | --- | --- |
| Compose overlays are supported runtime entrypoints | Public, edge, TLS, private, OIDC, MCP, and UI overlays are all documented and callable | `Makefile`, README refresh, and compose/smoke runs on `2026-04-02` | `met` |
| Make targets are the primary user UX for direct, edge, TLS, and private scenarios | README now uses `make up`, `make up-mcp`, `make up-edge`, `make up-tls`, `make smoke-*`, `make up-ui`, `make test`, `make compat` | `README.md` refresh on `2026-04-02`; commands validated locally | `met` |
| Gateway config changes remain additive only | Existing config fields remain; new root field added in this phase is `policy_fragments` | `app/config.py` inspection on `2026-04-02` | `met` |
| Only new planned root config field is `policy_fragments` | `policy_fragments` was added; no other new root field was introduced for this phase | `app/config.py` inspection on `2026-04-02` | `met` |
| Networking-related config changes stay narrowly focused on host/proxy awareness | Runtime changes are compose/nginx/trace focused; no broad product-surface networking model added | Overlay and trace inspection on `2026-04-02` | `met` |
| Python remains the gateway/runtime implementation language | Gateway/runtime remains FastAPI/Python | Repo inspection on `2026-04-02` | `met` |
| No Go, Rust, Zig, or Erlang rewrite is part of the work | None of those runtimes were added | Repo inspection on `2026-04-02` | `met` |
| Existing OIDC and MCP examples continue to run without Azurite or Service Bus emulator | Both example stacks passed their smokes without those services | `make smoke-mcp` and `make smoke-oidc` passed on `2026-04-02` | `met` |

## Acceptance Criteria

### Networking

| Spec item | Evidence | Status |
| --- | --- | --- |
| The direct public compose path works end to end | `2026-04-02`: `make up && until curl -fsS http://localhost:8000/apim/startup >/dev/null; do sleep 1; done && curl -fsS http://localhost:8000/apim/health >/dev/null && curl -fsS http://localhost:8000/api/echo >/dev/null` exited `0` | `met` |
| The edge-proxy compose path works end to end | `2026-04-02`: `make up-edge && make smoke-edge` exited `0` and printed `Edge smoke passed` | `met` |
| The TLS edge path works end to end with repo-generated development certificates | `2026-04-02`: `make up-tls && make smoke-tls` exited `0` and printed `Edge smoke passed` over `https://apim.localtest.me:8443` | `met` |
| The private/internal mode exposes no host-facing APIM entrypoint | `2026-04-02`: `make smoke-private` printed `Host port 8000 is unavailable, as expected.` and exited `0` | `met` |
| Private-mode smoke verification succeeds from the internal probe container | `2026-04-02`: `make smoke-private` exited `0` with the compose-network probe run | `met` |
| Forwarded-header behavior is visible in traces and preserved to the upstream echo/MCP target | `2026-04-02`: `make smoke-edge` and `make smoke-tls` passed with forwarded host/proto assertions; trace field test is green | `met` |
| MCP requests succeed through direct, edge, TLS, and private scenarios | `2026-04-02`: `make smoke-mcp`, `make smoke-edge`, `make smoke-tls`, and `make smoke-private` all exited `0` | `met` |

### Policy Compatibility

| Spec item | Evidence | Status |
| --- | --- | --- |
| Unit tests exist for each newly supported policy primitive | `tests/test_policy_golden.py` covers `set-variable`, `set-query-parameter`, `set-body`, `include-fragment`; full suite passed on `2026-04-02` | `met` |
| Curated APIM sample fixtures execute through the compatibility harness | `2026-04-02`: `make compat` exited `0` and reported supported/adapted fixture passes | `met` |
| Supported and adapted fixtures are enforced in CI | GitHub Actions workflow includes a dedicated `Compatibility Harness` job that runs `make compat` | `.github/workflows/ci.yml` added on `2026-04-02` | `met` |
| Unsupported fixtures are reported clearly in CI output | The CI workflow runs `make compat` directly, so unsupported summaries remain visible in job logs without failing the job | `.github/workflows/ci.yml` plus local `make compat` output on `2026-04-02` | `met` |
| Existing policy behavior remains unchanged unless explicitly covered by new fixture expectations | Full policy and gateway suites remained green after the phase work | `2026-04-02`: `uv run --extra dev pytest -q` => `70 passed, 1 skipped` | `met` |

### Regression

| Spec item | Evidence | Status |
| --- | --- | --- |
| `uv run --extra dev pytest -q` remains green | `2026-04-02`: `70 passed, 1 skipped` | `met` |
| The current OIDC smoke flow remains green | `2026-04-02`: `make smoke-oidc` exited `0` and printed `OIDC smoke passed` | `met` |
| The current MCP smoke flow remains green | `2026-04-02`: `make smoke-mcp` exited `0` and printed `MCP smoke passed` | `met` |
| The new networking smoke flows remain green | `2026-04-02`: `make smoke-edge`, `make smoke-tls`, and `make smoke-private` all exited `0` | `met` |

## Repo Surface Checks

| Check | Evidence | Status |
| --- | --- | --- |
| README quick-start commands match the current supported UX | README now uses `make` entrypoints for direct, MCP, edge, TLS, OIDC, private, UI, and teardown | `README.md` refresh on `2026-04-02` | `met` |
| `make up-ui` is usable with shipped example config | Example configs now ship a stable dev tenant key and management endpoints are reachable | `tests/test_gateway.py::test_shipped_example_configs_enable_management_plane`; `make up-ui` + `curl http://localhost:8000/apim/management/status -H 'X-Apim-Tenant-Key: local-dev-tenant-key'` succeeded on `2026-04-02` | `met` |
