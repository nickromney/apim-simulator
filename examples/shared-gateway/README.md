# Shared Gateway with RBAC Segregation

This example shows the cost-saving pattern of running **one** APIM instance
shared by several workloads, with each workload's access segregated by policy
and RBAC instead of by paying for separate gateways.

## The Azure pattern being simulated

In Azure, each AKS workload gets a managed identity (usually via Workload
Identity federation). The workload requests an Entra ID token and calls the
shared APIM instance. APIM validates the JWT and only routes the request if
the token carries the right claim for the API being called. One Premium or
Standard instance then serves many teams.

At the wire level a managed identity token is just an OIDC JWT from a
client-credentials-style grant, so locally we simulate it with Keycloak
service-account clients:

| Azure | This example |
| --- | --- |
| AKS workload managed identity | Keycloak client with `serviceAccountsEnabled` (`workload-team-a`, `workload-team-b`) |
| Entra ID app role / RBAC claim | Keycloak realm role (`team-a`, `team-b`) in `realm_access.roles` |
| Shared APIM instance | One simulator gateway with three APIs |
| Per-API authorisation | Route-level `authz.required_roles` (same enforcement as `validate-jwt` claims) |

## Run it

```bash
make up-shared
make smoke-shared
```

The smoke test fetches client-credentials tokens for both workloads and
verifies the segregation matrix:

| Caller | `/team-a/echo` | `/team-b/echo` | `/shared/echo` |
| --- | --- | --- | --- |
| no token | 401 | 401 | 401 |
| `workload-team-a` | 200 | 403 | 200 |
| `workload-team-b` | 403 | 200 | 200 |

## Manual calls

```bash
TOKEN=$(curl -s \
  -d grant_type=client_credentials \
  -d client_id=workload-team-a \
  -d client_secret=workload-team-a-demo-secret \
  http://localhost:8180/realms/subnet-calculator/protocol/openid-connect/token | jq -r .access_token)

curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/team-a/echo   # 200
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/team-b/echo   # 403
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/shared/echo   # 200
```

The client secrets in this example are intentional local demo values, in line
with the repository's security note.

## Entra-Shaped Claims Variant

The default config authorizes on Keycloak realm roles. If your production
policies match on the workload's *application id* instead — the common Entra
pattern of checking `azp`/`appid` for the managed identity's client — run the
variant config, which uses `required_claims` on the `azp` claim (Keycloak and
Entra v2 tokens both carry the caller's client id there):

```bash
SHARED_APIM_CONFIG_PATH=/app/examples/shared-gateway/apim.entra-claims.json make up-shared
make smoke-shared
```

The segregation matrix is identical, so the same smoke test passes.

## Notes

- Products in this config use `require_subscription: false` because identity
  comes from the JWT; you can layer subscription keys on top by flipping the
  flags, which then demonstrates APIM's two-axis model (subscription =
  metering/product membership, JWT = caller identity).
- To add a new tenant team, add a Keycloak client + realm role, then a new
  API block with `required_roles` — no gateway code changes.
