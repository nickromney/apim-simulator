"""Run local policy guide journeys against published gateway and broker ports."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from xml.sax.saxutils import escape

import httpx

TENANT = {"X-Apim-Tenant-Key": os.environ.get("APIM_TENANT_KEY", "local-dev-tenant-key")}
BROKER_ADMIN = {"Authorization": "Bearer " + os.environ.get("POLICY_BROKER_ADMIN_KEY", "local-broker-admin")}


def manage(client, method, path, body=None):
    response = client.request(method, "/apim/management/" + path, headers=TENANT, json=body)
    response.raise_for_status()
    return response.json()


def create_api(
    client,
    identifier,
    policy,
    *,
    operation_method="POST",
    operation_path="/echo",
    backend="http://mock-backend:8080/api",
):
    manage(client, "PUT", "apis/" + identifier, {"name": identifier, "path": identifier, "upstream_base_url": backend})
    manage(
        client,
        "PUT",
        f"apis/{identifier}/operations/call",
        {"name": "Call", "method": operation_method, "url_template": operation_path},
    )
    manage(client, "PUT", "policies/api/" + identifier, {"xml": policy})


def messaging(client, broker):
    for path in [
        "queues/orders",
        "topics/orders",
        "topics/orders/subscriptions/billing",
        "topics/orders/subscriptions/audit",
        "topics/api-logs",
        "topics/api-logs/subscriptions/analytics",
        "queues/alerts",
    ]:
        broker.put("/" + path, headers=BROKER_ADMIN).raise_for_status()
    for path in [
        "/queues/orders/consume",
        "/queues/alerts/consume",
        "/topics/orders/subscriptions/billing/consume",
        "/topics/orders/subscriptions/audit/consume",
        "/topics/api-logs/subscriptions/analytics/consume",
    ]:
        for _ in range(10001):
            drained = broker.post(path, headers=BROKER_ADMIN)
            drained.raise_for_status()
            if drained.json()["message"] is None:
                break
        else:
            raise AssertionError("Local lab broker did not drain")
    payload = {"order": 42, "customer": "Local"}
    xml = '<policies><inbound><send-service-bus-message topic-name="orders" namespace="local" message-id="@(context.RequestId.ToString())" time-to-live="00:10:00" response-variable-name="result"><message-properties><message-property name="Customer">Local</message-property></message-properties><payload>@(context.Request.Body.As&lt;string&gt;(preserveContent: true))</payload></send-service-bus-message></inbound><backend><forward-request /></backend><outbound /><on-error /></policies>'
    create_api(client, "policy-servicebus", xml)
    response = client.post("/policy-servicebus/echo", json=payload)
    assert response.status_code == 200 and json.loads(response.json()["body"]) == payload
    deliveries = []
    for subscription in ["billing", "audit"]:
        path = f"/topics/orders/subscriptions/{subscription}/consume"
        deliveries.append(broker.post(path, headers=BROKER_ADMIN).json()["message"])
        assert broker.post(path, headers=BROKER_ADMIN).json()["message"] is None
    assert deliveries[0] == deliveries[1]
    assert json.loads(deliveries[0]["payload"]) == payload
    assert deliveries[0]["properties"] == {"Customer": "Local"} and deliveries[0]["ttl_seconds"] == 600
    queue_xml = (
        xml.replace('topic-name="orders"', 'queue-name="orders"')
        .replace("<backend><forward-request /></backend>", "<backend />")
        .replace(
            "</send-service-bus-message>",
            '</send-service-bus-message><return-response><set-status code="201" reason="Created" /></return-response>',
        )
    )
    manage(client, "PUT", "policies/api/policy-servicebus", {"xml": queue_xml})
    assert client.post("/policy-servicebus/echo", json=payload).status_code == 201
    assert (
        json.loads(broker.post("/queues/orders/consume", headers=BROKER_ADMIN).json()["message"]["payload"]) == payload
    )
    sender = {"Authorization": "Bearer " + os.environ.get("POLICY_BROKER_SENDER_KEY", "local-broker-sender")}
    assert broker.post("/queues/orders/consume", headers=sender).status_code == 403
    print(
        "PASS Service Bus: topic fanout, independent consumers, queue, payload/properties/TTL, immediate 201 and sender-only access"
    )


def logging(client, broker):
    manage(
        client,
        "PUT",
        "loggers/policy-logger",
        {"logger_type": "azure_eventhub", "eventhub": {"name": "api-logs", "endpoint_uri": "local"}},
    )
    xml = '<policies><inbound><set-variable name="message-id" value="@(Guid.NewGuid())" /><log-to-eventhub logger-id="policy-logger" partition-id="0">@("request:" + context.Variables["message-id"].ToString() + "\\n" + context.Request.ToHttpMessage(1024))</log-to-eventhub></inbound><backend><forward-request /></backend><outbound><log-to-eventhub logger-id="policy-logger" partition-id="1">@("response:" + context.Variables["message-id"].ToString() + "\\n" + context.Response.ToHttpMessage(1024))</log-to-eventhub></outbound><on-error /></policies>'
    create_api(client, "policy-logging", xml)
    response = client.post(
        "/policy-logging/echo?hello=world",
        content="unchanged-body",
        headers={"Authorization": "Bearer private-demo", "X-Example": "retained"},
    )
    assert response.status_code == 200 and response.json()["body"] == "unchanged-body"
    request = broker.post("/topics/api-logs/subscriptions/analytics/consume", headers=BROKER_ADMIN).json()["message"]
    result = broker.post("/topics/api-logs/subscriptions/analytics/consume", headers=BROKER_ADMIN).json()["message"]
    request_id = request["payload"].split("\n", 1)[0].removeprefix("request:")
    response_id = result["payload"].split("\n", 1)[0].removeprefix("response:")
    assert request_id == response_id and request["partition_id"] == "0" and result["partition_id"] == "1"
    assert "Authorization:" not in request["payload"] and "private-demo" not in request["payload"]
    assert "retained" in request["payload"] and "unchanged-body" in request["payload"]
    assert "HTTP/1.1 200" in result["payload"]
    print(
        "PASS advanced logging: buffered request/response consumption, correlation, partitions, credential filtering and preserved bodies"
    )


def external(client, broker):
    token_expr = escape(
        '@(context.Request.Headers.GetValueOrDefault("Authorization","scheme param").Split(\' \').Last())',
        {'"': "&quot;"},
    )
    xml = f'<policies><inbound><set-variable name="token" value="{token_expr}" /><send-request mode="new" response-variable-name="tokenstate" timeout="20" ignore-error="true"><set-url>http://policy-broker:8081/introspection</set-url><set-method>POST</set-method><set-header name="Authorization" exists-action="override"><value>Basic dXNlcm5hbWU6cGFzc3dvcmQ=</value></set-header><set-header name="Content-Type" exists-action="override"><value>application/x-www-form-urlencoded</value></set-header><set-body>@("token=" + (string)context.Variables["token"])</set-body></send-request><choose><when condition="@((bool)((IResponse)context.Variables[&quot;tokenstate&quot;]).Body.As&lt;JObject&gt;()[&quot;active&quot;] == false)"><return-response><set-status code="401" reason="Unauthorized" /></return-response></when></choose></inbound><backend><forward-request /></backend><outbound /><on-error /></policies>'
    create_api(client, "policy-external", xml)
    assert client.post("/policy-external/echo", headers={"Authorization": "Bearer inactive"}).status_code == 401
    assert (
        client.post(
            "/policy-external/echo", content="original", headers={"Authorization": "Bearer active-local-token"}
        ).json()["body"]
        == "original"
    )
    manage(
        client,
        "PUT",
        "named-values/policy-broker-sender",
        {"value": os.environ.get("POLICY_BROKER_SENDER_KEY", "local-broker-sender"), "secret": True},
    )
    alert = '<policies><inbound /><backend><forward-request /></backend><outbound><choose><when condition="@(context.Response.StatusCode >= 500)"><send-one-way-request mode="new" timeout="20"><set-url>http://policy-broker:8081/queues/alerts/messages</set-url><set-method>POST</set-method><set-header name="Authorization" exists-action="override"><value>Bearer {{policy-broker-sender}}</value></set-header><set-header name="Content-Type" exists-action="override"><value>application/json</value></set-header><set-body>{"payload":"APIM Alert"}</set-body></send-one-way-request></when></choose></outbound><on-error /></policies>'
    create_api(client, "policy-alert", alert, operation_path="/fail")
    assert client.post("/policy-alert/fail").status_code == 503
    message = None
    for _ in range(30):
        message = broker.post("/queues/alerts/consume", headers=BROKER_ADMIN).json()["message"]
        if message:
            break
        time.sleep(0.1)
    assert message["payload"] == "APIM Alert"
    print(
        "PASS external services: reference-token introspection grants/denials, body preservation and one-way backend-error alert"
    )


def main():
    with (
        httpx.Client(
            base_url=os.environ.get("APIM_BASE", "http://localhost:8900"), trust_env=False, timeout=30
        ) as client,
        httpx.Client(
            base_url=os.environ.get("BROKER_BASE", "http://localhost:8901"), trust_env=False, timeout=10
        ) as broker,
    ):
        for _ in range(60):
            try:
                client.get("/apim/health").raise_for_status()
                broker.get("/health").raise_for_status()
                break
            except httpx.HTTPError:
                time.sleep(0.5)
        messaging(client, broker)
        logging(client, broker)
        external(client, broker)
        sys.path.insert(0, str(Path(__file__).parent))
        import external_composition

        print(external_composition.run(client))
        import throttling

        print(throttling.run(client))
        import authoring

        print(authoring.run(client))
        import expressions_graphql

        print(expressions_graphql.run(client, broker))
    print("Local policy guide live verification passed")


if __name__ == "__main__":
    main()
