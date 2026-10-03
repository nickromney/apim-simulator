# Local APIM security implementation

Goal: implement every actionable defect and local security outcome in the
2026-10-02 audit, retaining the completed tutorial/policy workflows. Work stays
on `codex/apim-tutorial-validation`; existing uncommitted policy changes are
preserved. Local UI/API workflows suffice; no Azure CLI/editor/cloud emulator.

## Plan and ownership

1. Root: authorize trace capture/read/reload consistently; integrate security
   middleware/routers; protect persisted secrets; backup/restore; dependency
   remediation; host-port lab and final regression/review.
2. Identity: signed operator/portal identities, least privilege and API/workspace
   scope, local administrator conditional access, request actor attribution.
3. Network/TLS: trusted ingress, final-version protocol checks, local isolation,
   real client/backend mTLS, certificate lifecycle, signed workload identities.
4. Governance/monitoring: enforce secure configuration and locks, durable
   attributed audit, posture/threat findings, bounded WAF/body/concurrency rules.
5. Integration: local security lab with TLS ingress, protected backends/vault,
   replicated gateways and client failover; verify positive and negative paths.

## Done criteria

- [x] All four confirmed defects have negative regression tests.
- [x] Secure ingress ignores untrusted metadata and enforces network permissions.
- [x] TLS handshakes and client/backend certificates enforce real trust; lifecycle
      tests cover expiry, revocation, rotation and unsuitable protocols.
- [x] Signed management/portal/workload identity, scoped roles and conditional
      administrator access have permission/denial tests.
- [x] Secrets remain encrypted in persisted config/backups; vault and certificate
      lifecycle use explicit local workload identity permissions.
- [x] Ingress WAF/body/concurrency/abuse controls reject attacks without changing
      accepted payloads, and local backend perimeter rejects bypass calls.
- [x] Durable attributed audit, anomaly findings and posture checks survive restart.
- [x] Governance policies and locks prevent insecure/accidental changes.
- [x] Backup restoration and gateway/region failure/failover run through host ports.
- [x] Lockfile advisories are upgraded or individually mitigated with documented
      reachability and regression evidence; no untriaged high/critical item.
- [x] Earlier eleven tutorial and thirteen policy workflows remain functional.
- [x] Focused/full Python, lint/format/types/build/shell/Compose checks and final
      adversarial review pass, with remaining limitations stated precisely.

## Progress

2026-10-02: audit complete; implementation started. Defaults secure where changing
trust; deliberate marker/header teaching flows require explicit development mode.

2026-10-02: all listed local outcomes implemented. Eleven tutorial journeys and
thirteen policy journeys passed against rebuilt localhost services; the security
lab passed 22 TLS-host-port checks including signed vault secret/certificate
rotation and gateway failover. Full Python suite: 1,107 passed, one optional
Keycloak integration skipped. Shell suite: 41 passed. Dependency remediation,
remaining reviewed scanner records and local navigation backport have dedicated
reports. Adversarial review fixed scoped trace ownership, recovery downgrades,
callout certificate isolation and obsolete TLS transport lifecycle. Final UI
interaction checks, production Docker build and aggregate verification passed.
