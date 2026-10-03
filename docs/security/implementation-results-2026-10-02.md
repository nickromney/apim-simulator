# Local APIM security implementation results

The defects and missing local controls in the
[2026-10-02 audit](secure-api-management-2026-10-02.md) have been implemented on
`codex/apim-tutorial-validation`. The original audit records the state before
these changes. No Azure subscription or Developer SKU was needed.

| Guide outcome | Implemented local behavior |
| --- | --- |
| Secure API access | Signed JWT validation, subscription/product checks, scoped management and portal identities, trusted client certificates, existing policy throttling. Public trace capture requires a scoped debug credential unless explicitly enabled for a teaching example. Reload requires management authorization. |
| Network security | Public-disabled/Internal mode enforces approved socket peers. Only configured proxies can attest forwarding metadata; the edge replaces incoming claims. Outbound host/CIDR restrictions cover backends, redirects, callouts, resolvers, JWT key discovery and vault access. Docker gateways have no published port. |
| Transport security | TLS 1.2/1.3 ingress, cryptographic incoming certificate chain/expiry/revocation checks, backend CA/name verification and real client certificates. Stored certificates rotate without preserving the old HTTPX transport. |
| Identity and access | Signed operator roles with API/workspace scope; readers/operators cannot alter resources or retrieve subscription-key inventories. Trusted issuer MFA and compliant-device claims gate administrative roles. Portal subjects are signed and private responses disable browser caching. Workload tokens require signing keys and explicit audience grants; backends verify signature, audience, issuer, expiry and subject permissions. |
| Secrets and data | Authenticated encryption protects the complete persisted configuration and recovery snapshots. Local vault reads use signed workload identity and mTLS, preserve request-local snapshots, and reflect versionless secret rotation. Stored PKCS12 client certificates are fetched with signed workload identity over mTLS and rotate on subsequent requests; temporary private-key files are removed immediately after SSL context loading. |
| Logging and detection | Bounded durable SQLite security events attribute management changes to verified subjects, survive restart and omit credentials/bodies. Threat windows detect authentication failures, validation failures and abusive requests. Posture identifies unauthenticated, unused or governance-noncompliant endpoints. |
| Governance | Deny/audit modes enforce HTTPS, backend verification, vault-backed secret values, private gateway access and ownership tags. Parent deletion locks protect API descendants; rejected edits/imports leave runtime and persisted state intact. Imports preserve local trust settings. |
| Recovery | Encrypted backup/restore validates configuration and retains destination region. Restore rejects weakened identity, certificates, proxy trust, outbound limits, subscription gates, governance, ingress or audit. Two gateways behind a bounded health-aware edge continue serving while one region is stopped. |

## Verification

The [security lab](../../examples/apim-security/README.md) runs positive and
negative checks through published localhost TLS ports. Its
[live results](live-results-2026-10-02.json) record the individual assertions.
The full Python suite passes 1,107 tests, with one optional Keycloak integration skipped. All 41 shell tests pass. Python lint/format, YAML/Markdown checks, operator UI/Astro builds, the operator UI Docker build and Backstage app/backend builds pass. The live security lab passes 22 checks.

Operator console interaction checks verify memory-only credentials, signed-token
and demo-key mode isolation, expiry handling, reader/operator metadata views and
direct access within an API scope.

Focused tests additionally exercise actual TLS handshakes, revoked/expired and
untrusted certificates, signed vault reads, DNS pinning, JWT algorithms and
expiry, scoped roles, bounded bodies/concurrency, durable audit restart,
inherited locks, and atomic encrypted recovery.

All eleven original tutorial journeys and all thirteen policy-guide journeys
were rerun successfully against rebuilt local services. The policy lab retained
body-preserving traces, callout composition, named-value rotation, GraphQL
resolvers and independent pub/sub consumers.

Dependency upgrades, the local upstream navigation fix, and remaining transitive advisory reachability evidence
are recorded in the [dependency remediation report](dependency-remediation-2026-10-02.md). Python, operator UI and
Astro graphs scan clean. The optional Backstage graph retains advisory records
with documented affected paths; a clean scan is not claimed for that graph.

## Intentional local semantics

MFA/device conditions validate claims from a trusted local issuer; they do not
enroll devices or supply an authenticator. The WAF uses explicit bounded rules
for common injection patterns and request limits. It does not reproduce every
managed WAF signature or cloud volumetric DDoS capacity. Two local regions model
failure and recovery on one machine. Management edits replicate through
backup/restore rather than a distributed consensus service.

Teaching configurations explicitly opt into simulated certificate/forwarding
headers, unsigned demo workload identities or public traces when needed. Secure
configurations leave those switches disabled. This project remains a local
development and validation simulator.
