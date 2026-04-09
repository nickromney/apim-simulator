# 9 - Customise Developer Portal

Source: [Tutorial: Access and customise the developer portal](https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-developer-portal-customize)

Simulator status: Not appropriate

## Why This Does Not Map Directly

The Microsoft developer portal is a managed CMS and public API-consumer website. `apim-simulator` is a local gateway and operator tool, not a CMS clone.

## Closest Local Equivalent

From the repo root:

```bash
export APIM_BASE=http://localhost:8000
export APIM_TENANT_KEY=local-dev-tenant-key
```

Use the operator console instead:

```bash
make up-ui
```

Open:

- gateway: `http://localhost:8000`
- operator console: `http://localhost:3007`

The operator console is the supported local surface for:

- policy editing
- trace browsing
- replaying requests
- inspecting and rotating subscriptions

## Shortcut

If you want the scripted shortcut instead of running the commands manually:

```bash
./tutorial09.sh
./tutorial09.sh --verify
```

Unlike the manual path above, `tutorial09.sh` starts the UI stack itself and
waits for both the gateway and operator console to become reachable.

Expected key `./tutorial09.sh --verify` output:

```text
Waiting for operator console at http://localhost:3007
Operator console is available at http://localhost:3007
Gateway is available at http://localhost:8000

Verifying the closest local equivalent
$ curl -sS -H "X-Apim-Tenant-Key: local-dev-tenant-key" "http://localhost:8000/apim/management/status"
{
  "gateway_scope": "gateway",
  "service_name": "apim-simulator"
}

$ curl -sS "http://localhost:3007"
{
  "status_code": 200
}
```

## Guidance

If you need to rehearse developer-portal workflows, use real Azure APIM.
If you need to rehearse gateway behaviour, policies, traces, and management edits locally, stay in the simulator.
