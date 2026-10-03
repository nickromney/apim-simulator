# APIM security coverage audit — 2026-10-02

**Answer: no.** The policy tutorial work established specific local workflows;
it did not establish complete security coverage. This read-only audit compares
the current branch and uncommitted policy work with Microsoft's
[Secure your Azure API Management deployment](https://learn.microsoft.com/en-us/azure/api-management/secure-api-management)
guide, updated 2026-09-28. The goal is to reproduce security outcomes locally.
Missing cloud-branded controls need local equivalents rather than exclusions.

**Scope:** gateway, management, portal, policy runtime, Compose/edge configuration,
frontend dependency graphs, and supporting tests. Python 3.13/FastAPI/HTTPX,
React/Vite operator UI, static Astro demo and optional Backstage portal.
No implementation, dependency or running-service configuration was changed.
Three high-severity defects and one medium-severity control-plane defect were
confirmed; coverage gaps and scanner advisories are reported separately.

## Confirmed security defects

### 1. High: inline tracing bypasses protected trace access

With tracing enabled, a caller can send `X-Apim-Trace: true` without a tenant,
admin or scoped debug credential. `_TraceContext.read` accepts the header at
`app/request_pipeline.py:1462`; `attach_trace` puts the complete base64 payload
in the response at lines 345–346. Protecting the trace retrieval routes does
not protect this inline copy. A synthetic policy callout returning a private
access token reproduced disclosure to a public caller while both protected
retrieval routes rejected the same caller with 403. Named-value masking does
not recognize arbitrary private data returned by a callout.

**Fix:** require the same authorized scoped debug access for capture and delivery;
keep any legacy convenience behind an explicit local development setting.
**Breakage risk:** existing examples using unauthenticated `X-Apim-Trace` need
scoped credentials or an explicit development opt-in.

### 2. High: forwarded metadata has no trusted-proxy boundary

`app/request_pipeline.py:1062` uses the first client-supplied X-Forwarded-For
value as the client IP. `app/proxy.py:149` accepts X-Forwarded-Proto directly.
Synthetic probes changed IP-policy rejection from 403 to 200 and HTTPS-only
route rejection from 404 to 200 by forging those headers. The edge adds to the
incoming forwarding chain, so the first attacker-provided hop remains present.

**Fix:** configure trusted ingress proxies, derive direct-call identity/scheme
from the socket, and sanitize metadata at the edge.
**Breakage risk:** current local scripts that simulate callers with forwarding
headers must explicitly opt into a trusted simulation mode.

### 3. High: version selection can bypass HTTPS-only restrictions

The outer route check enforces protocols at `app/proxy.py:289`, but
`_match_versioned_candidate` at lines 210–229 omits that check for the selected
version. A synthetic version set with an HTTP-capable Original API and an
HTTPS-only v1 returned 200 for v1 over plain HTTP, even without forged headers.

**Fix:** apply protocol and online-state restrictions to the final selected API.
**Breakage risk:** calls currently reaching a disallowed protocol will be rejected;
that is the intended API contract.

### 4. Medium: reload bypasses tenant management authorization

When tenant management is enabled and no separate admin token is configured,
`POST /apim/reload` executes its reload callback without the tenant key.
`app/main.py:378–383` checks only the optional admin token. A synthetic probe
received 403 from management status but 200 from reload and observed the callback.
This gives an unauthorized caller a control-plane action, even though it does
not let them select arbitrary configuration contents.

**Fix:** authorize reload through the configured management/admin boundary.
**Breakage risk:** unauthenticated reload scripts need the configured tenant key.

## Security simulation gaps

These are coverage gaps, including deliberate adaptations already documented
in the repository. They are not claims that a production cloud deployment has
been breached.

| Guide area | Implemented evidence | Remaining local work |
| --- | --- | --- |
| Gateway validation and authorization | JSON/schema/parameter/header/status validators; signed JWT checks; roles/scopes; keyed throttling and call/bandwidth quotas | Negative security journeys spanning the whole OWASP mapping; ingress body/concurrency limits; trusted source for IP counters |
| Network isolation | Unpublished gateway port in private Compose shape; separate backend networks and IP policy examples | Enforce public-access/internal-mode configuration at runtime; explicit ingress/egress rules; WAF and bounded abuse/load-protection lab; backend perimeter reject tests |
| TLS and certificates | TLS-terminating Nginx edge and HTTP redirect; outbound HTTPX certificate verification | Real client and backend mTLS, certificate-chain/expiry/revocation validation, certificate rotation and protocol/cipher negative checks; fix forwarded-scheme and version bypasses |
| Identity and access | OIDC/JWT verification, local role checks, subscription scope and independent key regeneration, protected management routes | Reader/operator/contributor/workspace/content-editor permissions; administrator MFA/device conditions; signed portal identity; sender/token permission grants rather than marker-only identity |
| Secrets and data | Secret masking, environment inputs and separate local-vault reader role; versionless secret rotation verified | Encrypted persisted inline secrets; certificate vault lifecycle; managed workload identity for secret access without shared static credentials |
| Telemetry and investigation | OTEL, bounded local request/resource/activity logs, metrics/alerts and pub/sub event logging | Close trace bypass; durable retention and actor-attributed change audit; threat/anomaly detection and unused/unauthenticated endpoint recommendations |
| Governance | Resource metadata/tags and import compatibility inspection | Enforceable security configuration rules, posture evaluation, resource delete locks and negative management tests |
| Recovery and resilience | Atomic configuration saves and backend-pool failure handling | Backup/restore round trips, gateway-instance/zone/region failure simulations and client failover evidence |

Client-certificate authentication is currently a header-marker adaptation:
`app/security.py:405` reads subject/issuer/thumbprint claims from headers;
`app/backend_pool.py:156` adds a marker instead of presenting a TLS certificate.
A required-thumbprint probe returned 401 without the header and 200 with a forged
matching header. This verifies the adapter's comparison behavior, not mTLS.
The shipped edge does not establish and sanitize verified client certificates.
The `validate-client-certificate` XML policy is explicitly unsupported in the
current fidelity contract. A local CA plus real TLS peers is the appropriate
simulation to add.

The service's `public_network_access_enabled=False` and internal network metadata
do not themselves deny a request to an exposed gateway. The private Compose
shape is an independently selected topology. Network security settings need
observable enforcement or explicit rejection when that topology is absent.
The merged TLS example still publishes the plaintext 8088 proxy alongside the
8080 redirect and 9443 TLS endpoint; enabling that example does not by itself
require HTTPS.

Management authorization uses shared tenant/admin keys (`app/security.py:442–466`),
not scoped operator principals. The consumer portal openly documents a configured
user header as its stand-in identity (`app/portal.py:1–8`). Those mechanisms can
exercise product workflows, but do not demonstrate least privilege or secured
portal sign-in. Inline named values remain strings in configuration and atomic
JSON persistence; `secret=true` controls masking rather than encryption at rest.

## Dependency advisories

Scans completed without modifying lockfiles. Counts are scanner matches, not
confirmed exploitable application paths. The machine-readable
[dependency evidence](dependency-audit-2026-10-02.json) preserves installed
versions, advisory links and scanner-provided upgrade information.

| Surface | Reported matches |
| --- | --- |
| Operator UI npm graph | 4 high, 1 moderate, 2 low affected packages |
| Static Astro demo npm graph | 1 critical, 9 high, 1 moderate, 1 low affected packages |
| Optional Backstage Yarn graph | 23 critical, 109 high, 122 moderate, 22 low advisory records |
| Python runtime requirements | 7 affected packages, 34 deduplicated package/advisory records; scanner did not supply severity |

The demo locks Astro 6.1.6, and both browser graphs lock Vite 7.3.2. Their scanners
report fixes available. Backstage includes critical vm2 sandbox advisories.
Reachability needs per-advisory triage: the Astro demo is statically built and
served with Nginx, and Vite build/dev-server advisories are not automatically
runtime gateway exploits. Python findings include AnyIO, Click, cryptography,
idna, PyJWT, Starlette and urllib3. Some scanner recommendations require major
upgrades; one PyJWT advisory has no listed fixed version. Do not blindly apply
an audit-fix upgrade across these graphs.

**Breakage risk:** dependency upgrades can alter FastAPI/Starlette, token validation,
frontend builds and Backstage plugins; upgrade and validate each runtime separately.

## Promise and hygiene checks

README explicitly describes development/testing use and warns against public
exposure. The fidelity contract also discloses marker-only certificate behavior.
There is no certification or production-hardening promise to substantiate.
Public Compose publishes `${APIM_GATEWAY_PORT}:8000` without a loopback binding,
so a local-only posture still depends on host networking/firewall configuration.
Loopback binding is a useful default; LAN workflows would need an explicit override.

No live credential was confirmed by the bounded tracked-file/pattern review;
demo credentials and a vendored-script pattern hit were distinguished from leaks.
This is not a complete Git-history secret audit. The expression evaluator uses
an AST allowlist and blocks private attributes; an `eval` search hit alone is
not evidence of arbitrary request-driven code execution. Pub/sub SQL uses
parameterized queries. The frontend innerHTML search hits clear containers
with literal empty strings, not untrusted markup.

## Verification and next work

Existing security-related tests: **74 passed**, covering JWT validation, content
and parameter/header/status validation, forwarding behavior, scoped debug
credentials, management and product authoring. [Synthetic negative probes](negative-probes-2026-10-02.json) exposed
the defects above. The earlier 927-test full run is useful regression evidence,
but does not certify these missing security cases. A separate focused run passed
41 certificate, protocol, IP-filter and managed-identity tests, plus 83 focused
portal/debug/media/named-value tests; those adapter tests
coexist with the reproduced boundary failures.

Priority: first close the trace and route/trusted-ingress defects; then build
real local mTLS and scoped management identity; then add governance, egress/WAF,
threat monitoring and recovery journeys. Each should have allow/deny or
failure/recovery assertions through published host ports. Preserve the existing
local demo workflows with explicit compatibility settings where necessary.
