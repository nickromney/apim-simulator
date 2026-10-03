# Local security governance and monitoring

These controls execute locally through the gateway and management API. Their
configuration defaults preserve teaching examples: governance is disabled,
security-event persistence is disabled, and ingress enforcement is disabled.
Enable the desired controls in the gateway configuration before starting the
lab, or assign governance through its protected management endpoint.

The configuration checks follow the APIM controls in Microsoft's
[policy definitions](https://learn.microsoft.com/en-us/azure/api-management/policy-reference)
and [security baseline](https://learn.microsoft.com/en-us/azure/api-management/security-baseline).
They check encrypted API protocols, backend certificate verification,
vault-backed secret named values, private gateway configuration, required API
tags, and resource deletion locks. `audit` returns findings while allowing a
change; `deny` rejects noncompliant changes before persistence or publication.
Delete locks apply independently of mode, including an attempted mutation
that removes both the lock and its resource. API locks also protect child
operations, schemas, revisions, releases,
API/operation policies, GraphQL resolvers, and revision snapshot children from
deletion; edits to retained resources remain allowed. Unlocking requires a separate
authorized configuration change.

```json
{
  "security_governance": {
    "mode": "deny",
    "encrypted_protocols": true,
    "backend_certificate_verification": true,
    "vault_secret_named_values": true,
    "required_api_tags": ["owner"],
    "delete_locks": {"apis": ["orders"]}
  },
  "security_observability": {
    "enabled": true,
    "database_path": "/data/security-events.sqlite3",
    "max_events": 10000
  },
  "security_ingress": {
    "enabled": true,
    "waf_mode": "detect",
    "max_body_bytes": 1048576,
    "max_concurrent_requests": 100,
    "requests_per_window": 1000,
    "window_seconds": 60
  }
}
```

Use a writable persistent mount for `/data`; the SQLite store retains events
across process restart and limits retention to `max_events`. It stores event
kind, timestamp, verified actor or socket peer, method, path, status, reason,
and a small allowlist of metadata. It excludes arbitrary bodies, headers,
credentials and query strings. Signed management subjects provide actor
attribution; legacy shared keys identify a shared credential rather than a
human. Security-setting changes rebuild the sink when needed, and disabling
recording still writes the management operation to the previously active sink.

Threat rules count matching event kinds/statuses per actor in a configured
time window. Default rules identify repeated authentication failures, ingress
denials, and validation failures. Rules provide recommendations and findings;
their counters describe the retained local event window. The posture endpoint
also reports declared unauthenticated routes and routes not observed in that
window. A lack of observations is a review signal, rather than evidence that
an endpoint has never been used.

The ingress middleware applies to gateway traffic by default. It limits body
bytes and body-read time, URL size, simultaneous requests, and requests per
socket peer. Key and event caps bound rate-counter storage. It uses the socket
peer for network/rate admission; a caller-provided `X-Forwarded-For` cannot
change those decisions. `allowed_client_networks` enables CIDR admission.
The private governance control additionally requires internal/private service
metadata and explicit private socket-peer CIDRs in network_security.private_peer_cidrs.

The WAF inspects bounded path/query/body surfaces for specific SQL
injection, script injection and traversal forms. It decodes URL escapes twice
to test encoded probes. `detect` records findings and forwards the original
body; `block` returns 403. Inspection decodes a copy regardless of the declared
Content-Type, including absent or binary types. Allowed payloads remain
byte-for-byte intact, and individual rule families can be disabled for a teaching
fixture. Management
authoring paths are excluded so policy XML can be saved deliberately; they
remain protected by control-plane authorization.

The OWASP-inspired negative matrix in `tests/test_security_ingress.py` covers
injection, encoded probes, peer-header spoofing and bounded resource admission.
[OWASP API4](https://api-security.owasp.org/editions/2023/en/0xa4-unrestricted-resource-consumption/)
provides the resource-consumption rationale. The detector implements those
specific rule families; application object/field/business authorization still
requires its own policy and tests.

Protected endpoints:

| Method and path | Outcome |
| --- | --- |
| GET/PUT `/apim/management/security/governance` | Inspect/assign checks and deletion locks |
| GET `/apim/management/security/posture` | Review configuration and endpoint findings |
| GET `/apim/management/security/events?limit=100&after=0` | Read retained events |
| GET `/apim/management/security/threats` | Read rule findings and recommendations |

Whole-service recovery requires a global writer. API/workspace-scoped grants
cannot restore the entire gateway. An encrypted snapshot that disables or
weakens currently active governance, locks, ingress, audit, encryption, MFA,
device conditions or operator trust anchors is rejected before mutation.
Resource recovery runs the ordinary configuration checks as well.

Focused verification:

```sh
uv run --extra dev pytest tests/test_security_governance_monitoring.py tests/test_security_ingress.py tests/test_security_runtime_recovery.py
```
