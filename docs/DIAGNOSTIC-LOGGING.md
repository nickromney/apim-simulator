# Diagnostic logging — adapted subset

Configure what the gateway logs for each request the way APIM's Application
Insights and Azure Monitor settings do, then read the records locally.

## Turn it on

A diagnostic needs a logger from your configuration. Create the service-wide
Application Insights diagnostic:

```bash
curl -s -X PUT http://localhost:8000/apim/management/diagnostics/applicationinsights \
  -H 'X-Apim-Tenant-Key: local-dev-tenant-key' -H 'Content-Type: application/json' \
  -d '{
        "logger_id": "appinsights",
        "sampling_percentage": 50,
        "always_log_errors": true,
        "log_client_ip": true,
        "verbosity": "information",
        "http_correlation_protocol": "W3C",
        "frontend_request": {"headers_to_log": ["user-agent"], "body_bytes": 1024},
        "backend_response": {"headers_to_log": ["content-type"], "body_bytes": 1024}
      }'
```

Call an API a few times, then read what was kept, newest first:

```bash
curl -s http://localhost:8000/apim/management/monitoring/telemetry \
  -H 'X-Apim-Tenant-Key: local-dev-tenant-key'
```

Expected result: about half the successful calls appear, every failed call
appears, and each record has the URL, status, duration, client IP, a W3C
operation ID, and the requested headers and body bytes. Filter with
`?api_id=orders` or `?destination=azuremonitor`. `DELETE` on the same address
clears the store.

## Settings

| Setting | Effect |
| --- | --- |
| Diagnostic ID | `applicationinsights` or `azuremonitor`. Each is a separate destination with its own records |
| `sampling_percentage` | Share of requests kept, 0-100. Default 100. Sampling is deterministic, so tests repeat |
| `always_log_errors` | Keep responses with status 400 and above even when sampling drops them |
| `log_client_ip` | Add `client_ip` to each record |
| `http_correlation_protocol` | `W3C` reads the trace ID from `traceparent`, `Legacy` (default) reads `Request-Id`, `None` records no operation ID. Missing headers get a generated ID |
| `verbosity` | Lowest `trace` policy severity kept: `verbose`, `information` (default) or `error` |
| `frontend_request`, `backend_request`, `backend_response`, `frontend_response` | `headers_to_log` (names, case-insensitive), `body_bytes` (0-8192), and `data_masking` rules for headers and, on the frontend request, query parameters. `Mask` replaces the value with `***`; `Hide` drops it |

An API-scope diagnostic, written to
`/apim/management/apis/{api_id}/diagnostics/{diagnostic_id}`, replaces the
service-scope one for that API, as the per-API settings in the portal do.

The `trace` policy writes messages that the record carries in `traces`:

```xml
<trace source="orders" severity="information">
  <message>@("looked up " + context.Request.Url.Path)</message>
  <metadata name="kind" value="lookup" />
</trace>
```

## Differences from APIM

| APIM | Here |
| --- | --- |
| Records go to Application Insights or a Log Analytics workspace | Records go to an in-memory store of the last 2,000, read through the management API |
| Bodies are logged from the stream | A streamed body is recorded as `body_unavailable: streamed`. Set `proxy_streaming` to `false` to log bodies |
| W3C correlation also sends `traceparent` to the backend | The backend request is not changed |
| Diagnostic settings reject unknown loggers and out-of-range values | The same checks return `400` |
| The portal's defaults switch on client IP and error logging | An unset setting is off, except sampling, which defaults to 100% |

References: [Application Insights integration](https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-app-insights),
[diagnostic REST API](https://learn.microsoft.com/en-us/rest/api/apimanagement/diagnostic/create-or-update),
[trace policy](https://learn.microsoft.com/en-us/azure/api-management/trace-policy).
Evidence: `tests/test_diagnostic_logging.py` (`OBS-DIAGNOSTIC-TELEMETRY`).
