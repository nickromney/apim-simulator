# Coverage of the "Complete Azure API Management Masterclass" video

A maintainer check of the simulator against Azure Innovation Station's
[Complete Azure API Management Masterclass](https://www.youtube.com/watch?v=MdIsc38YruM)
(March 2025, two hours). The video walks through Microsoft's APIM hands-on lab
in the portal. It complements the architecture-first
[deep dive check](APIM-DEEP-DIVE-COVERAGE.md).

**Covered** means a working local equivalent exists. **Adapted** means it works
with a documented difference. **Out of scope** means there is no useful local
equivalent.

| # | Chapter | Status | Where it lives |
| --- | --- | --- | --- |
| 1 | Gateway, management plane, developer portal | Covered | Gateway on `:8000`, management API and operator console, `/apim/portal` |
| 2–3 | Azure subscription, RBAC, creating the instance | Out of scope | `make up` replaces provisioning. Signed control-plane grants model per-API roles |
| 3 | Echo API; "missing subscription key" until the requirement is lifted | Covered | `/api/echo` (anonymous) and `/demo/echo` (key required) in the default stack |
| 4 | Publish the portal, CORS for "Try it", developer sign-up, Starter and Unlimited (approval) | Covered | Portal publish workflow, `cors` policy, portal identities, `approval_required` products |
| 5 | Visual editor: pages, layouts, menus, media, styles, publish | Adapted | Draft, preview and publish customisation with pages, navigation, logo and colours. No layouts, pop-ups or menus limited to signed-in users |
| 6 | Email notifications and templates | Adapted | Added after this review: [EMAIL-NOTIFICATIONS.md](EMAIL-NOTIFICATIONS.md). Messages go to a local outbox |
| 7 | Products: rate limit and quota at product scope, legal terms, subscription limits, group visibility, publish | Covered | Products with policies, `terms`, `subscriptions_limit`, group visibility |
| 8 | API from scratch: suffix, URL template parameters, tags | Covered | Operator console and management API authoring |
| 9 | OpenAPI import, HTTP backend behind an HTTPS gateway | Covered | OpenAPI import (console, CLI, script); TLS stacks |
| 10 | Import Azure OpenAI with token policies; Function App import | Adapted | `make up-ai` with `llm-token-limit`; Azure resource import itself needs Azure |
| 11 | Calling APIs with keys; Starter throttled after 5 calls | Covered | Subscription keys, `rate-limit`, `quota` |
| 12 | Scopes, `<base />`, `context.Deployment.Region`, `cache-lookup`/`cache-store` | Covered | Policy scopes and effective-policy view; local cache |
| 13 | `find-and-replace`, conditional field removal, `set-header` delete, header and query from `context` | Covered | Those policies, plus `context.Product?.Name`, which is null-safe after this review |
| 13 | `xml-to-json` | Adapted | Added after this review: [XML-JSON-TRANSFORMS.md](XML-JSON-TRANSFORMS.md) |
| 13 | Trace in the test console | Covered | `x-apim-trace: true`, `/apim/trace/{id}`, operator console traces |
| 14 | Named values, secrets, Key Vault references, `{{name}}` in policy | Covered | Named values with local Key Vault overrides |
| 15 | `mock-response` from an example | Covered | `mock-response` with authored examples |
| 16 | Webhook through `send-one-way-request` | Covered | `send-one-way-request` |
| 17 | `return-response` on a backend 500; `on-error` with `context.LastError` | Covered | Both, with `LastError` fields |
| 18 | Versions: path, header, query; version sets; portal version picker | Covered | Version sets and portal version selection |
| 19 | Revisions: `;rev=2`, make current, change log, roll back | Covered | Revisions, releases with notes shown in the portal |
| 20 | Analytics, Log Analytics, Application Insights: sampling, verbosity, client IP, correlation, header/body capture, per-API override | Adapted | Added after this review: [DIAGNOSTIC-LOGGING.md](DIAGNOSTIC-LOGGING.md). Plus OTEL/LGTM dashboards and local monitoring |

## What changed after this review

- `context.Product?.Name` and other `?.` access return null instead of failing.
- `xml-to-json` and `json-to-xml` policies.
- Diagnostics shape local request telemetry, with the `trace` policy.
- Local email notifications, templates and outbox.

## Remaining gaps

1. A starter configuration that matches a new Azure instance, with the Echo API,
   Starter (5 calls per 60 seconds, 100 per week) and Unlimited products, and
   the Administrators, Developers and Guests groups, so the lab can be followed
   step by step.
2. Portal layouts, pop-ups and menus shown only to signed-in users.
