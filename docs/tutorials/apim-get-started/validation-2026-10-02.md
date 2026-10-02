# Local tutorial validation — 2026-10-02

Branch: `codex/apim-tutorial-validation`.

All eleven Microsoft Get Started tutorial outcomes were exercised locally using
management APIs, the consumer portal, local observability services, and Bruno CLI.
No Azure subscription or Developer SKU was used.

| Tutorial | Verified local outcome |
| --- | --- |
| 1 Import | Untouched public Petstore OpenAPI 3.0.4 imports 19 operations; pending-pet and repeated-tag calls reach the local backend. Swagger 2 fixture imports 20 operations in focused tests. |
| 2 Product | Unpublished product hidden from consumer catalog; explicit publish reveals it; subscription access, terms acceptance and signup limit work. |
| 3 Mock | Operation metadata and mock policy produce the expected local response without a backend call. |
| 4 Protect | Custom header appears; three calls succeed, fourth returns 429/Retry-After; a call succeeds after renewal. |
| 5 Monitor | Gateway metrics, management activity logs, sampled resource logs and Fired alert action-group event; Prometheus/Loki/Tempo export verified through Grafana. |
| 6 Debug | API-scoped temporary credential, trace header and lookup; wrong API and expiry headers; response body unchanged. |
| 7 Revisions | Isolated revision definitions, direct revision calls, release promotion, offline and unknown revision rejection. |
| 8 Versions | Clone independently editable version; Original remains reachable; Segment/Header/Query routing and consumer request selectors. |
| 9 Portal | Branding/theme/pages/image draft edits, protected preview, anonymous draft privacy, publish, public media/pages, signup approval and try-it. |
| 10 Client | Bruno CLI imports, edits, links product, subscribes, sets policy, tests successful/throttled calls and exports OpenAPI; 11 requests/tests pass on consecutive runs. |
| 11 Catalog | Local center link, all APIs and optional OpenAPI definitions, live source create/edit/delete synchronization, unlink clearing and relink. |

## Reproduce

```bash
make -C examples/apim-tutorials smoke
```

For a separate local stack, set `STACK_SLOT=7`. Each tutorial runs setup and verify
against published localhost ports and the smoke runner cleans up its isolated
stack between tutorials. Setup writes tutorial configuration; tutorial 10 verify
repeats authoring and tutorial 6 verify creates temporary debug credentials.

## Validation

- Python suite: 843 passed, one external-service integration test deselected.
- Focused product partial-publication regression: eight passed.
- Full shell suite: 41 passed (including 17 tutorial tests).
- Python Ruff lint and formatting, ShellCheck, Bash syntax, diff whitespace checks.
- Operator console: npm lint and TypeScript/Vite production build passed.
- Live Docker journeys: 01–05 and 06–11 passed after correcting discovered issues;
  the Bruno CLI ran with its default safe sandbox and pinned version 4.2.0.

## Local service semantics

The client interfaces use local tenant authentication and IDs. They reproduce the
tutorial outcomes rather than ARM authentication, Azure CLI or editor extension
behavior. Petstore 3.0.4 support is a documented local extension to Microsoft's
currently documented OpenAPI 3.0.3 import subset. The checked-in public documents
are unchanged; an explicit local backend URL routes their calls to the mock service.

The portal editor uses forms for branding, theme, pages and image uploads. Local
monitoring observations, alerts and temporary credentials are in memory; portal
content, diagnostic settings/rules and the catalog link persist in configuration.
Action groups produce inspectable local events. Catalog synchronization is
immediate on management writes. These are concrete local equivalents of the
source tutorials' hosted services.
