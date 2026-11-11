# 5 - Monitor published APIs

Microsoft Learn equivalent tutorial: [Tutorial: Monitor published APIs](https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-use-azure-monitor)

Send requests and inspect their traces, metrics, and logs.

## Start the monitoring stack

Save any edits you want to keep before stopping the default stack.
[Save and restore your edits](../../LOCAL-LIFECYCLE.md#save-edits-before-stopping)

The monitoring stack uses HTTPS for Grafana. Install `mkcert` and run
`mkcert -install` to trust its local CA. From the repository root, generate
the certificates and start the stack.

```bash
export APIM_BASE=http://localhost:8000
export APIM_TENANT_KEY=local-dev-tenant-key
make ensure-certs
make down
./docs/tutorials/apim-get-started/tutorial05.sh --setup
```

Setup creates `tutorial-api` and the subscription key `tutorial-key`.

## Trace a request

Send a request through the gateway with tracing enabled.

```bash
curl -i -sS -H "Ocp-Apim-Subscription-Key: tutorial-key" \
  -H "x-apim-trace: true" \
  -H "x-correlation-id: monitor-health" \
  "$APIM_BASE/tutorial-api/health"
```

The response has HTTP 200, an `x-apim-trace-id` header, and a JSON body with
`"status": "ok"`. List recent traces and look for `monitor-health` in
`correlation_id`.

```bash
curl -sS -H "X-Apim-Tenant-Key: $APIM_TENANT_KEY" \
  "$APIM_BASE/apim/management/traces"
```

Open [Grafana](https://lgtm.apim.127.0.0.1.sslip.io:8443) to inspect the
gateway's metrics, logs, and traces.

## Local metrics, logs and alert rules

The script enables gateway resource logs through
`PUT /apim/management/monitoring/diagnostic-settings` and creates a failed-request
rule through `PUT /apim/management/monitoring/alert-rules/tutorial-failures`.
A deliberately missing route produces a failure and fires the local action group.
Inspect tenant-protected `metrics`, `activity-logs`, `resource-logs` and `alerts`
under `/apim/management/monitoring/`. Activity logs record management writes;
resource logs record sampled gateway calls without consuming bodies. Alert events
include Fired/Resolved states and the local action-group name. Local observations
live in memory and reset on restart; diagnostic settings and rules persist in the
configuration. Alert counts use one-second buckets for windows up to 24 hours.

## Verify telemetry

```bash
./docs/tutorials/apim-get-started/tutorial05.sh --verify
```

The check reports two HTTP 200 traces from setup and confirms that gateway
telemetry reaches Grafana's Prometheus, Loki, and Tempo backends.
