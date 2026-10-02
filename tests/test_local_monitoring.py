from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient
from starlette.requests import Request

from app.config import ApiConfig, GatewayConfig, OperationConfig, TenantAccessConfig
from app.local_monitoring import AlertRule, MonitoringStore
from app.main import create_app

TENANT = {"X-Apim-Tenant-Key": "tenant"}
BASE = "/apim/management/monitoring"


def test_monitoring_diagnostics_activity_metrics_and_alert_transitions() -> None:
    cfg = GatewayConfig(
        allow_anonymous=True,
        tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant"),
        apis={
            "sample": ApiConfig(
                name="Sample",
                path="sample",
                upstream_base_url="",
                operations={
                    "get": OperationConfig(
                        name="Get",
                        method="GET",
                        url_template="/",
                        policies_xml='<policies><inbound><return-response><set-status code="200" reason="OK" /><set-body>original</set-body></return-response></inbound></policies>',
                    )
                },
            )
        },
    )
    with TestClient(create_app(config=cfg)) as client:
        assert client.get(BASE + "/metrics").status_code == 403
        assert client.get("/sample").text == "original"
        assert client.get(BASE + "/resource-logs", headers=TENANT).json()["items"] == []
        assert client.put(BASE + "/diagnostic-settings", headers=TENANT, json={"gateway_logs": True}).status_code == 200
        assert (
            client.put(
                BASE + "/alert-rules/failures",
                headers=TENANT,
                json={"name": "Gateway failures", "metric": "failed_requests", "threshold": 0, "window_seconds": 300},
            ).status_code
            == 200
        )

        assert client.get("/sample").text == "original"
        assert client.get("/missing").status_code == 404
        assert client.get("/missing").status_code == 404
        metrics = client.get(BASE + "/metrics", headers=TENANT).json()
        assert metrics["requests"] == 4
        assert metrics["failed_requests"] == 2
        logs = client.get(BASE + "/resource-logs", headers=TENANT).json()["items"]
        assert len(logs) == 3
        assert logs[0]["api_id"] == "sample"
        assert all("body" not in log for log in logs)
        alerts = client.get(BASE + "/alerts", headers=TENANT).json()
        assert alerts["firing_rules"] == ["failures"]
        assert len(alerts["items"]) == 1
        assert alerts["items"][0]["state"] == "Fired"
        client.put(BASE + "/alert-rules/failures", headers=TENANT, json={"name": "Gateway failures", "enabled": False})
        assert client.get(BASE + "/alerts", headers=TENANT).json()["items"][-1]["state"] == "Resolved"
        activities = client.get(BASE + "/activity-logs", headers=TENANT).json()["items"]
        assert any(log["path"].endswith("diagnostic-settings") and log["status_code"] == 200 for log in activities)
        assert (
            client.put(BASE + "/diagnostic-settings", headers=TENANT, json={"sampling_percentage": 101}).status_code
            == 422
        )


def test_alert_windows_count_more_than_the_request_detail_buffer_and_expire(monkeypatch) -> None:
    import app.local_monitoring as monitoring

    clock = [1000.0]
    monkeypatch.setattr(monitoring.time, "time", lambda: clock[0])
    cfg = GatewayConfig()
    app = SimpleNamespace(state=SimpleNamespace(gateway_config=cfg))
    request = Request(
        {"type": "http", "method": "GET", "path": "/sample", "query_string": b"", "headers": [], "app": app}
    )
    store = MonitoringStore()
    for _ in range(6001):
        store.record(request, status_code=500, duration_seconds=0.01)
    assert len(store.requests) == 5000
    assert len(store.request_buckets) == 1
    rules = {
        metric: AlertRule(name=metric, metric=metric, threshold=6000, window_seconds=10)
        for metric in ("requests", "failed_requests")
    }
    store.evaluate(rules)
    assert store.fired_rules == {"requests", "failed_requests"}
    assert all(alert["value"] == 6001 for alert in store.alerts)
    clock[0] = 1011.0
    store.evaluate(rules)
    assert store.fired_rules == set()
    assert [alert["state"] for alert in list(store.alerts)[-2:]] == ["Resolved", "Resolved"]
    assert store.metrics()["requests"] == 6001
    clock[0] = 87401.0
    store.evaluate(rules)
    assert store.request_buckets == {}
    assert not store.bucket_seconds
