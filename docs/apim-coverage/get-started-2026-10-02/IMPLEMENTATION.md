# Local tutorial simulation implementation

## Goal and constraints

Rehearse all eleven tutorial outcomes locally using existing gateway,
management and consumer-portal surfaces. Keep Azure as an optional comparison
baseline. Preserve backwards-compatible loaded example configurations while
correcting management authoring defaults. Do not claim external Azure SDK wire
compatibility from a passing local workflow.

## Functional phases

1. **Import, publish, mock and protect:** import the unmodified tutorial Petstore
   documents; author product state, limits and terms; preserve policies on edits;
   verify mock examples, subscription grants, throttling and renewal.
2. **Observe, debug, revise and version:** verify telemetry export; expose local
   metrics, activity/resource logs and alert rules; mint API-scoped expiring
   debug credentials; test revision isolation/promotion/offline routing; verify
   version schemes, original routing and published version discovery.
3. **Customize, author and catalog:** edit draft portal branding/pages, preview
   and publish; retain consumer sign-up and try-it; exercise editor authoring;
   link a local API Center model, synchronize API inventory/definitions, observe
   updates and unlink, removing the synchronized catalog entries as documented.

Each phase includes user-facing endpoints or UI and meaningful tests. Update
the numbered tutorial scripts and guides to exercise the resulting workflows.

## Done criteria

- All eleven scripted local tutorial workflows pass against published ports.
- Exact Petstore import inputs are verified, rather than substituted silently.
- Draft publication, debug authorization, catalog synchronization and product
  settings have focused regressions covering failure/isolation paths.
- Application tests, relevant lint/formatting and diff review pass.
- Documentation identifies simulated infrastructure and client-interface
  differences without treating Azure hosting as an implementation exclusion.
