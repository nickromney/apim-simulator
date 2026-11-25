# Self-hosted gateway mode

Use this mode when an application should call a gateway in its own network
while that gateway receives configuration from a managed APIM instance. The
simulator demonstrates the important topology:

- callers use the self-hosted gateway URL for API traffic;
- the self-hosted process makes outbound configuration requests only;
- APIs are assigned to `managed` or one or more named gateways;
- a self-hosted process continues with its last good configuration when the
  managed instance is unavailable.

This follows the Azure concepts described in [the self-hosted gateway
overview](https://learn.microsoft.com/en-us/azure/api-management/self-hosted-gateway-overview)
and [self-hosted gateway provisioning
guidance](https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-provision-self-hosted-gateway).

## Run the local example

Prerequisites: Docker and the repository's `uv` environment.

```bash
make -C examples/self-hosted-gateway compose-config
make -C examples/self-hosted-gateway verify STACK_SLOT=8
make -C examples/self-hosted-gateway down STACK_SLOT=8
```

The example publishes the managed instance on port `8000` and the
self-hosted gateway on `8001` (both are shifted by `100 * STACK_SLOT`). Its
backend is attached only to the self-hosted gateway's internal network.

Expected smoke results include:

- the self-hosted gateway pulls a token-authenticated snapshot;
- `/remote/` is `404` on the managed gateway because that API is assigned only
  to `remote`;
- `/remote/` succeeds through the self-hosted port and reaches the remote
  backend locally;
- a policy update on the managed instance appears after the one-second poll;
- stopping the managed instance does not interrupt the last-known-good API
  configuration.

## Configuration

Register gateways in the managed configuration and assign each API:

```json
{
  "gateways": {
    "remote": {
      "location": {"name": "remote-network"},
      "description": "Gateway beside the application",
      "access_token": "replace-with-a-local-secret"
    }
  },
  "apis": {
    "orders": {
      "name": "Orders",
      "path": "orders",
      "upstream_base_url": "http://orders-backend:8080",
      "gateways": ["managed", "remote"],
      "operations": {
        "list": {"name": "List orders", "method": "GET", "url_template": "/"}
      }
    }
  }
}
```

When `gateways` is omitted, a service-level API defaults to `["managed"]`.
Existing workspace APIs continue to use their workspace gateway placement
unless they have an explicit API assignment. The existing workspace host
selection remains available for local workspace gateway experiments.

The managed instance exposes a read-only, per-gateway snapshot:

```text
GET /apim/management/gateways/{gateway_id}/configuration
Authorization: Bearer <gateway token>
If-None-Match: "<previous etag>"
```

It returns `200` with an `ETag`, or `304` when unchanged. Missing credentials
return `401`; invalid credentials return `403`; expired configured tokens
return `401`. The snapshot filters APIs to those assigned to the requested
gateway and does not include the gateway access token.

The self-hosted instance uses:

| Variable | Meaning |
| --- | --- |
| `APIM_SELF_HOSTED_GATEWAY=true` | Enable configuration-pull mode |
| `APIM_SELF_HOSTED_GATEWAY_ID` | Gateway resource ID to serve |
| `APIM_SELF_HOSTED_GATEWAY_CONFIG_URL` | Managed snapshot endpoint |
| `APIM_SELF_HOSTED_GATEWAY_TOKEN` | Gateway access token |
| `APIM_SELF_HOSTED_GATEWAY_POLL_INTERVAL` | Poll interval in seconds |

Inspect sync state at
`GET /apim/self-hosted-gateway/status`. The self-hosted management API is
read-only; configuration changes belong on the managed instance.

## Simulator and Azure boundaries

This is an adapted local simulation, not an Azure control-plane client. It
uses a configured token rather than Azure-issued token lifecycle, does not
implement encrypted Kubernetes Secret distribution, certificate rotation,
gateway heartbeat/availability orchestration, or production HA. The snapshot
is intentionally a local executable configuration document. Azure's gateway
runtime, supported versions, deployment channels, and operational guarantees
must be confirmed against the linked Microsoft documentation for a real
deployment.
