# APIM fidelity and operator workflow

Requested 2026-09-30. Implement in order, using Microsoft Learn as the behavior reference and bounded implementation agents. The user updated subsequent agents to GPT-6-luna.

## 1. Fidelity contracts and product publication

- Define the supported subset, local adaptations, unsupported inputs, and evidence requirements in `FIDELITY-CONTRACTS.md`.
- Reconcile scope/capability/roadmap contradictions.
- Keep unpublished products out of consumer discovery while allowing authorized gateway calls.
- Verify subscription denial, unpublished access, mixed products, and portal visibility with focused regressions.

## 2. OpenAPI import

- Project operation identifiers/display names, parameters, request/response representations, examples, and API schemas into the existing config model.
- Match documented server selection, required-query translation, and GET/HEAD/OPTIONS body omission.
- Use one projection for management and Terraform imports; retain operation policy on matching reimport.
- Explicitly report or reject unsupported constructs; do not infer OpenAPI security definitions as gateway authentication.
- Verify routing, validation, mocks, metadata, and reimport behavior; run Python lint/format gates.

## 3. API → operation → policy → test → trace

- Extend the existing React console with API/operation selection, metadata-driven request inputs, XML policy editing, product scope, effective-policy inspection, and a readable request trace.
- Include import and API/operation authoring needed for this workflow. Reuse existing local management endpoints; do not claim Azure ARM wire compatibility.
- Tighten save failure handling where needed for GUI edits.
- Verify frontend lint/type/build and exercise the browser workflow against a local gateway.

## Completion criteria

Focused regressions and applicable application tests pass; changed Python and frontend files pass formatting/lint/type/build; adversarial diff review findings are resolved. Documentation states remaining adaptations and unverified Azure behavior. No full APIM, portal CMS, cloud administration, or pixel-identical Azure portal claim.

## Progress

- Phase 1: complete. Publication/authorization/portal suite: 151 passed using `uv`; contract ownership collection passed.
- Phase 2: implemented with GPT-6-luna; 165 focused tests passed. OpenAPI 2.0 JSON and 3.0.x through 3.0.3 are the bounded target; 3.1 remains excluded.
- Phase 3: complete. Atomic saves, effective-policy inspection, API/operation authoring, and the operator workflow are implemented.
- Final verification: 737 Python tests passed, one Keycloak integration test skipped; branch coverage 84.62% passes the 82% ratchet. `UV_CACHE_DIR=/private/tmp/apim-simulator-uv-cache make lint-check`, full contract collection, `npm run check`, and `git diff --check` passed.
- Local Chrome verification: connection; metadata-driven integer path/query requests; response and structured trace; effective policy; policy save; API/operation edits preserving metadata and policies; API creation/OpenAPI import/operation creation and deletion; Header/Query/Segment API versions; custom API/global subscription key names; desktop/mobile layout without horizontal overflow. Browser tests used isolated temporary config and local ports 8017/3017.
- Adversarial review resolved query-template policy-copy loss, unsupported scalar path/header serialization, save refresh/selection races, pending-policy edit overwrite, and unsaved/pending-write navigation cases. Raw operation-ID matching on reimport follows the literal Microsoft documentation; live Azure comparison remains outstanding.
- No live Azure differential run or external Keycloak stack was available. Full APIM coverage, independent revision execution, full expression-engine parity, cloud services, and concurrent-writer/ETag coordination remain outside this implementation.
