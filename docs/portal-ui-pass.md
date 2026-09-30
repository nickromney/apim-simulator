# Azure APIM portal UI pass

Reference: read-only inspection of the deployed Azure APIM portal on 2026-09-30, using its APIs Design, Settings and Test views.

## Goal and scope

Make the local APIM Simulator familiar to an APIM portal user while preserving the supported management, policy editing, request testing, subscription and trace workflows. This pass changes the React UI only.

Observed patterns: compact blue header; Segoe UI typography; flat white panes with grey borders; API navigation alongside operation navigation; Design / Settings / Test tabs; compact settings fields and bottom save actions. The Design view presents frontend, inbound, backend and outbound policy stages. Test keeps operation selection alongside the request form.

Apply the visual shell, separate resource navigation and tab terminology now. Keep the existing XML policy editor as the supported Design tool. A graphical policy stage editor needs separate scope and validation; do not imply it exists. Include navigation only for usable simulator features. Do not reproduce Azure account, subscription or infrastructure controls.

## Constraints and verification

Preserve dirty-draft guards, loading guards, full-resource saves, effective-policy inspection, OpenAPI import, metadata-based tests and traces. Inspect the live reference without saving or sending requests. Check frontend lint, TypeScript and production build; review the diff and inspect the rebuilt local UI in the browser. Python changes are outside this pass.

## Creation and revision journey

The user extended the pass to compare API creation and revisions while away from the keyboard, with no new Azure infrastructure provisioned. A deliberately isolated `subnetcalc-simulator-comparison` API was created inside the existing service using the HTTP creation form. Its `GET /health` operation follows the platform subnet calculator route. The web service URL is `https://example.invalid/api/v1`: this comparison exercises management workflows without deploying or calling a backend.

Azure observations: API creation automatically creates current revision 1; Add revision clones the current API and chooses revision 2 automatically; revision 2 can edit an operation independently; its URL includes `;rev=2`. The revision table shows ID, creation time, description, URL, online and current status. Make current opens a dialog with optional public change-log notes. Revision 2 was created, its Health description edited, and it was subsequently made current with public change-log publication unchecked. Revision 1 remains online. Promotion initially failed because the management-group resource-type allowlist omitted `Microsoft.ApiManagement/service/apis/releases`; CLI activity logs identified `RequestDisallowedByPolicy`, despite the portal reporting a generic authorization error. Promotion succeeded after that allowlist was updated through its separate infrastructure workflow.

The local port includes the bounded HTTP/OpenAPI chooser, file or URL import, revision snapshots and selector routing, offline rejection, explicit promotion and release notes. Verification must cover edits to current revisions as well as explicit noncurrent requests; snapshot records must not become stale when ordinary API/operation editors save current state.

## Verified local outcomes

Browser checks created the HTTP comparison API and `GET /health`, observed automatic current revision 1, cloned revision 2, edited revision 2's operation description and policy, and verified revision 1 retained its original description. Local `return-response` fixtures returned revision-specific JSON without contacting a backend. The unqualified API URL returned revision 1 before promotion and revision 2 after promotion. An offline revision returned 404; it was then restored online. The local release note appears in Changelog.

The final Python suite passed 740 tests with one Keycloak integration skip; branch-aware coverage was 84.67%, above the 82% gate. Frontend lint/type/build and Python formatting/lint were checked. A model-representation golden fixture was refreshed only for the additive empty `definition` field after examining the exact difference.

The reproducible local runtime is in `examples/portal/subnetcalc-runtime.json`, loaded by `compose.portal-journey.yml`. It contains the two verified mock-response revisions and release note; it provisions no external infrastructure.

## Design refinement and identical OpenAPI import

The subsequent design pass reduces the masthead to branding and navigation,
moves credentials into a collapsible connection toolbar, and shows a focused
welcome screen while disconnected. The local-demo action reveals the form;
a successful connection collapses it. Browser verification caught and fixed
the grid CSS overriding the workspace's `hidden` attribute. Frontend lint,
TypeScript, production build, and the rebuilt container passed verification.

The identical `examples/portal/subnetcalc.openapi.json` document was imported
into `subnetcalc-openapi` in Azure and locally, separate from the revision demo.
Both have current revision 1, Health (`GET /health`) and IPv4 subnet information
(`POST /ipv4/subnet-info`), the same suffix and placeholder backend. Both match
operation IDs, display names, methods, paths, 200 response descriptions, JSON
content type, and the request example. The local Test view prepopulates the
imported JSON body. Azure was configured for HTTPS; local API defaults still
include HTTP and HTTPS. Backend connectivity was not exercised.

Concrete fidelity gaps from this differential check:

- Azure accepted the first document's inline complex request schema; the local
  importer rejected it. Moving it to a referenced component schema allowed
  both imports. The checked-in document is the final common input.
- Azure fills a missing operation description from the summary; local import
  leaves it null.
- Azure stores a generated schema resource ID plus `typeName`, `sample`, and
  `generatedSample`. Local import uses the component name as its schema ID,
  leaves `type_name` null, and retains the explicit example instead.

These are observed differences, not evidence of complete import fidelity.

The legibility follow-up separates API and operation navigation into full
columns instead of squeezing both into a single explorer card. Names and paths
wrap without ellipsis. The detail workspace uses 13px text, compact fields,
tighter spacing and smaller policy stages; the XML editor starts at 12 rows.
At narrower widths navigation occupies a row above details. Action labels use
Add API / Add operation to enter authoring, Create for a new operation, and Save
for edits. Lint, TypeScript, build and desktop browser verification passed.
