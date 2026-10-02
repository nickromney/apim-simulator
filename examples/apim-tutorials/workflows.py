"""Executable local equivalents for monitoring, debug and API Center tutorials."""

from __future__ import annotations

import argparse
import os
import time

import httpx

BASE = os.environ.get("APIM_BASE", "http://localhost:8000")
TENANT = {"X-Apim-Tenant-Key": os.environ.get("APIM_TENANT_KEY", "local-dev-tenant-key")}
API = os.environ.get("APIM_API_ID", "tutorial-api")
PATH = os.environ.get("APIM_API_PATH", "tutorial-api")
SUBSCRIPTION = {"Ocp-Apim-Subscription-Key": os.environ.get("APIM_SUBSCRIPTION_KEY", "tutorial-key")}


def management(client: httpx.Client, method: str, path: str, body=None):
    response = client.request(method, "/apim/management/" + path, headers=TENANT, json=body)
    response.raise_for_status()
    return response.json()


def monitoring(client: httpx.Client, phase: str) -> None:
    prefix = "monitoring/"
    if phase == "setup":
        management(client, "PUT", prefix + "diagnostic-settings", {"gateway_logs": True})
        management(
            client,
            "PUT",
            prefix + "alert-rules/tutorial-failures",
            {
                "name": "Tutorial gateway errors",
                "metric": "failed_requests",
                "threshold": 0,
                "window_seconds": 300,
                "action_group": "local-operators",
            },
        )
        assert client.get("/tutorial-monitor-missing-route").status_code == 404
    metrics = management(client, "GET", prefix + "metrics")
    assert metrics["requests"] > 0 and metrics["failed_requests"] > 0
    assert management(client, "GET", prefix + "activity-logs")["items"]
    assert management(client, "GET", prefix + "resource-logs")["items"]
    alerts = management(client, "GET", prefix + "alerts")
    assert any(item["state"] == "Fired" and item["action_group"] == "local-operators" for item in alerts["items"])
    print("Monitoring: metrics, activity/resource logs and local alert action group verified")


def debug(client: httpx.Client, phase: str) -> None:
    del phase
    prefix = "gateways/managed/"
    token = management(
        client,
        "POST",
        prefix + "listDebugCredentials",
        {
            "apiId": API,
            "purposes": ["tracing"],
            "credentialsExpireAfter": "PT1H",
        },
    )["token"]
    plain = client.get(f"/{PATH}/health", headers=SUBSCRIPTION)
    traced = client.get(f"/{PATH}/health", headers={**SUBSCRIPTION, "Apim-Debug-Authorization": token})
    assert traced.status_code == plain.status_code == 200 and traced.content == plain.content
    trace = management(client, "POST", prefix + "listTrace", {"traceId": traced.headers["Apim-Trace-Id"]})
    assert trace
    other_api = next(item["id"] for item in management(client, "GET", "apis") if item["id"] != API)
    other_token = management(client, "POST", prefix + "listDebugCredentials", {"apiId": other_api})["token"]
    wrong = client.get(f"/{PATH}/health", headers={**SUBSCRIPTION, "Apim-Debug-Authorization": other_token})
    assert wrong.status_code == 200 and "Apim-Debug-Authorization-WrongAPI" in wrong.headers
    short = management(
        client,
        "POST",
        prefix + "listDebugCredentials",
        {
            "apiId": API,
            "credentialsExpireAfter": "PT1S",
        },
    )["token"]
    time.sleep(1.1)
    expired = client.get(f"/{PATH}/health", headers={**SUBSCRIPTION, "Apim-Debug-Authorization": short})
    assert expired.content == plain.content and "Apim-Debug-Authorization-Expired" in expired.headers
    print("Debug: scoped credentials, trace lookup, wrong API and expiry verified; response body preserved")


def catalog(client: httpx.Client, phase: str) -> None:
    center = "api-centers/tutorial-center"
    if phase == "setup":
        management(client, "PUT", center, {"name": "Tutorial API Center"})
        management(client, "PUT", "api-center/link", {"center_id": "tutorial-center", "include_definitions": True})
        management(
            client,
            "PUT",
            "apis/catalog-probe",
            {"name": "Catalog probe", "path": "catalog-probe", "upstream_base_url": "http://mock-backend:8080/api"},
        )
        management(
            client,
            "PUT",
            "apis/catalog-probe/operations/health",
            {"name": "Health", "method": "GET", "url_template": "/health"},
        )
        items = management(client, "GET", center + "/apis")["items"]
        probe = next(item for item in items if item["id"] == "catalog-probe")
        assert "/health" in probe["definition"]["paths"]
        management(
            client,
            "PUT",
            "apis/catalog-probe",
            {
                "name": "Updated catalog probe",
                "path": "catalog-probe",
                "upstream_base_url": "http://mock-backend:8080/api",
            },
        )
        assert (
            next(
                item for item in management(client, "GET", center + "/apis")["items"] if item["id"] == "catalog-probe"
            )["title"]
            == "Updated catalog probe"
        )
        management(client, "DELETE", "apis/catalog-probe")
        assert all(item["id"] != "catalog-probe" for item in management(client, "GET", center + "/apis")["items"])
        management(client, "DELETE", "api-center/link")
        assert management(client, "GET", center + "/apis")["items"] == []
        management(client, "PUT", "api-center/link", {"center_id": "tutorial-center", "include_definitions": True})
    assert management(client, "GET", "api-center/link")["state"] == "Linked and syncing"
    items = management(client, "GET", center + "/apis")["items"]
    assert {item["id"] for item in items} == {item["id"] for item in management(client, "GET", "apis")}
    assert all(
        item["definition"]["openapi"] == "3.0.3" and item["environment"] and item["deployment"] for item in items
    )
    print("API Center: continuous create/edit/delete synchronization, definitions, unlink and relink verified")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", choices=["monitoring", "debug", "catalog"])
    parser.add_argument("phase", choices=["setup", "verify"])
    args = parser.parse_args()
    with httpx.Client(base_url=BASE, timeout=15, trust_env=False) as client:
        {"monitoring": monitoring, "debug": debug, "catalog": catalog}[args.workflow](client, args.phase)
