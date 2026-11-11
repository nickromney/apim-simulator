# 6 - Debug your APIs

Microsoft Learn equivalent tutorial: [Tutorial: Debug your APIs](https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-api-inspector)

Request a trace and inspect the gateway response and policy steps.

## Request a trace

From the repo root:

```bash
export APIM_BASE=http://localhost:8000
export APIM_TENANT_KEY=local-dev-tenant-key
```

These commands assume `tutorial-api` and `tutorial-key` already exist. If not,
run steps 1 and 2 first or use `./docs/tutorials/apim-get-started/tutorial02.sh --setup`.

Request a trace:

```bash
curl -i \
  -H "Ocp-Apim-Subscription-Key: tutorial-key" \
  -H "x-apim-trace: true" \
  "$APIM_BASE/tutorial-api/health"
```

Copy the `x-apim-trace-id` response header, then inspect the trace:

```bash
curl -sS "$APIM_BASE/apim/trace/<trace-id>"
```

You can also browse recent traces:

```bash
curl -sS -H "X-Apim-Tenant-Key: $APIM_TENANT_KEY" \
  "$APIM_BASE/apim/management/traces"
```

The trace includes the response status, upstream URL, and policy execution steps.

## Run the scripted exercise

To set up and verify the exercise with a script, run:

```bash
./docs/tutorials/apim-get-started/tutorial06.sh --setup
./docs/tutorials/apim-get-started/tutorial06.sh --verify
```

Setup sends a traced health request. Verification checks the stored trace for
an HTTP 200 response and the upstream URL `http://mock-backend:8080/api/health`.

## API-scoped debug credentials

To request and retrieve a trace with an API-scoped debug credential:

1. POST to `/apim/management/gateways/managed/listDebugCredentials` with the tenant
   header and `{"apiId":"tutorial-api","purposes":["tracing"],"credentialsExpireAfter":"PT1H"}`.
2. Call the gateway with the returned token in `Apim-Debug-Authorization`.
3. Read `Apim-Trace-Id`, then POST `{"traceId":"<returned-id>"}` to
   `/apim/management/gateways/managed/listTrace` with the tenant header.

A credential for another API or an expired credential produces debug response headers
without changing the gateway result. Debug tokens are temporary,
in memory, bounded to one hour, and removed from forwarded backend headers.
The existing `x-apim-trace` flow remains available for local compatibility.
