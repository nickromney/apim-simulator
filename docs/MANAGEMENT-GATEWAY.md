# Management Gateway Mode

Run two simulator instances in the shape of an Azure APIM self-hosted gateway
deployment. Callers and operators use one address, the management gateway. A
second instance, the serving gateway, holds the configuration and does the
gateway work.

| Instance | Compose service | Host address | Role |
| --- | --- | --- | --- |
| Management gateway | `apim-simulator` | `http://localhost:8000` | Serves no APIs. Routes every request to the serving gateway |
| Serving gateway | `apim-serving` | `http://localhost:8001` | An ordinary simulator. Owns the APIs, policies, management API, portal and traces |

## Start the stack

You need Docker with Compose. Ports 8000 and 8001 must be free; run
`make check-host-ports` to check.

```bash
make up-management-gateway
```

Both containers start, with the serving gateway first. The management gateway
starts once the serving gateway reports healthy. The serving gateway loads
`examples/basic.json`. To load a different file from the image, set
`APIM_CONFIG_SOURCE_PATH`.

To run it beside other stacks, shift the ports with a slot. Slot 7 publishes
the management gateway on 8700 and the serving gateway on 8701:

```bash
STACK_SLOT=7 make up-management-gateway
```

## Check the routing

```bash
curl -s http://localhost:8000/apim/management-gateway/status
```

Expected result: `"mode": "management-gateway"`, `"serves_apis_natively": false`,
and `"serving_gateway": {"status": "ready", ...}`.

Call an API through the management gateway:

```bash
curl -s http://localhost:8000/api/echo
```

The response comes from the mock backend behind the serving gateway. The same
call to `http://localhost:8001/api/echo` returns the same backend response.

A management write sent to the management gateway is stored on the serving
gateway:

```bash
curl -s -X PUT http://localhost:8000/apim/management/named-values/region \
  -H 'X-Apim-Tenant-Key: local-dev-tenant-key' -H 'Content-Type: application/json' \
  -d '{"value": "uksouth"}'
curl -s http://localhost:8001/apim/management/named-values/region \
  -H 'X-Apim-Tenant-Key: local-dev-tenant-key'
```

The second call returns `"value": "uksouth"`.

Run the full smoke check, which uses the published localhost ports:

```bash
make smoke-management-gateway
```

Expected result: four `ok` lines (status, gateway traffic, trace, management
write), then `management gateway smoke passed`. Use the same `STACK_SLOT` you
started the stack with.

Stop the stack with `make down`, or `STACK_SLOT=7 make down` for a slotted
stack.

## What the management gateway forwards

| Request | Handled by |
| --- | --- |
| `GET /apim/health`, `GET /apim/startup` | Management gateway (its own liveness) |
| `GET /apim/management-gateway/status` | Management gateway, which also checks the serving gateway's `/apim/startup` |
| Everything else: API routes, `/apim/management/*`, `/apim/reload`, `/apim/portal`, `/apim/trace/*`, `/` | Serving gateway |

When forwarding, the management gateway:

- keeps the method, path, query string, body and the caller's `Host` header;
- removes hop-by-hop headers, and lets the body's actual length set
  `Content-Length`;
- appends the caller's address to `X-Forwarded-For`. The serving gateway ignores
  that header unless the management gateway's address is in its
  `network_security.trusted_proxy_cidrs`;
- relays the serving gateway's status, headers and body unchanged, including
  streamed responses.

If the serving gateway cannot be reached, callers get `502` with an APIM-style
body `{"statusCode": 502, "message": ...}`. If it does not answer within 60
seconds, they get `504`.

## Turn the mode on yourself

Management gateway mode is set by environment variables on the simulator
process. It does not use the JSON config file.

| Variable | Required | Meaning |
| --- | --- | --- |
| `APIM_MANAGEMENT_GATEWAY` | Yes | `true` (or `1`, `yes`, `on`) turns the mode on. The instance then loads no config |
| `APIM_MANAGEMENT_GATEWAY_UPSTREAM` | Yes, when on | Serving gateway base URL, scheme and host only, for example `http://apim-serving:8000` |
| `APIM_MANAGEMENT_GATEWAY_NAME` | No | Self-hosted gateway name shown in the status response. Defaults to `self-hosted` |

If the flag is on and the upstream is missing or has a path, the process stops
at startup with an error naming `APIM_MANAGEMENT_GATEWAY_UPSTREAM`.

## How this differs from Azure

In Azure, the managed instance owns the configuration and also serves traffic
itself. A self-hosted gateway pulls its configuration from the managed
instance's configuration endpoint. Here, the serving gateway owns the one
configuration and the management gateway routes to it, so there is nothing to
keep in sync. When you replay requests against a real Developer-tier APIM with a
self-hosted gateway, send gateway traffic to the self-hosted gateway's address,
and management calls to the Azure management endpoint.
