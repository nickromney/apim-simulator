from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import ApiConfig, GatewayConfig, LoggerConfig, LoggerEventHubConfig, OperationConfig
from app.local_pubsub import BrokerStore, build_handler
from app.main import create_app
from app.messaging_config import BrokerBinding, LocalMessagingConfig


def test_topic_fanout_independent_consumption_queue_fifo_and_expiry(monkeypatch) -> None:
    store = BrokerStore()
    store.create("topics", "orders")
    store.create("topics", "orders", "billing")
    store.create("topics", "orders", "audit")
    first = {"payload": "one", "message_id": "one"}
    assert store.publish("topics", "orders", first) == 2
    assert store.consume("topics", "orders", "billing") == first
    assert store.consume("topics", "orders", "billing") is None
    assert store.consume("topics", "orders", "audit") == first
    store.create("queues", "work")
    store.publish("queues", "work", first)
    store.publish("queues", "work", {"payload": "two"})
    assert store.consume("queues", "work") == first
    assert store.consume("queues", "work")["payload"] == "two"
    monkeypatch.setattr("app.local_pubsub.time.time", lambda: 100)
    store.publish("queues", "work", {"payload": "expired", "ttl_seconds": 1})
    monkeypatch.setattr("app.local_pubsub.time.time", lambda: 102)
    assert store.consume("queues", "work") is None
    with pytest.raises(LookupError):
        store.publish("queues", "missing", first)


def test_broker_http_sender_cannot_create_or_consume() -> None:
    store = BrokerStore()
    server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(store, "admin", "sender"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False) as client:
            admin = {"Authorization": "Bearer admin"}
            sender = {"Authorization": "Bearer sender"}
            reader = {"Authorization": "Bearer local-vault-reader"}
            assert client.put("/secrets/token", headers=admin, json={"value": "first"}).status_code == 200
            assert client.get("/secrets/token", headers=reader).json() == {"value": "first"}
            assert client.get("/secrets/token", headers=sender).status_code == 403
            assert client.put("/secrets/token", headers=reader, json={"value": "second"}).status_code == 403
            assert client.put("/secrets/token", headers=admin, json={"value": "second"}).status_code == 200
            assert client.get("/secrets/token/version", headers=reader).json() == {"value": "second"}
            assert client.put("/queues/work", headers=sender).status_code == 403
            assert client.put("/queues/work", headers=admin).status_code == 200
            assert client.post("/queues/work/messages", headers=sender, json={"payload": "hello"}).status_code == 201
            assert client.post("/queues/work/consume", headers=sender).status_code == 403
            assert client.post("/queues/work/consume", headers=admin).json()["message"]["payload"] == "hello"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _gateway(store, policy, monkeypatch, *, allowed_client_ids=("system-assigned",), logger_client_id=None):
    monkeypatch.setenv("BROKER_SENDER_KEY", "sender")

    def transport(request):
        if request.url.host == "broker":
            assert request.headers["Authorization"] == "Bearer sender"
            parts = request.url.path.strip("/").split("/")
            try:
                store.publish(parts[0], parts[1], json.loads(request.content))
            except LookupError:
                return httpx.Response(404, json={"detail": "missing"})
            return httpx.Response(201, json={"accepted": True})
        return httpx.Response(200, content=request.content, headers={"content-type": "text/plain"})

    cfg = GatewayConfig(
        allow_anonymous=True,
        apis={
            "messages": ApiConfig(
                name="Messages",
                path="messages",
                upstream_base_url="http://backend",
                policies_xml=policy,
                operations={"send": OperationConfig(name="Send", method="POST", url_template="/")},
            )
        },
        local_messaging=LocalMessagingConfig(
            default_namespace="local",
            namespaces={"local": BrokerBinding(endpoint="http://broker", allowed_client_ids=list(allowed_client_ids))},
        ),
        loggers={
            "logs": LoggerConfig(
                logger_type="azure_eventhub",
                eventhub=LoggerEventHubConfig(
                    name="logs", endpoint_uri="local", user_assigned_identity_client_id=logger_client_id
                ),
            )
        },
    )
    return TestClient(create_app(config=cfg, http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport))))


def test_gateway_service_bus_topic_and_forwarded_body_preserved(monkeypatch) -> None:
    store = BrokerStore()
    store.create("topics", "orders")
    for name in ["billing", "audit"]:
        store.create("topics", "orders", name)
    policy = '<policies><inbound><send-service-bus-message id="send-order" topic-name="orders" namespace="local" time-to-live="00:10:00"><message-properties><message-property name="Customer">Local</message-property></message-properties><payload>@(context.Request.Body.As&lt;string&gt;(preserveContent: true))</payload></send-service-bus-message></inbound><backend><forward-request /></backend><outbound /><on-error /></policies>'
    with _gateway(store, policy, monkeypatch) as client:
        assert client.post("/messages", content="payload").text == "payload"
    a = store.consume("topics", "orders", "billing")
    b = store.consume("topics", "orders", "audit")
    assert a == b and a["payload"] == "payload" and a["properties"] == {"Customer": "Local"}
    assert a["ttl_seconds"] == 600


@pytest.mark.parametrize("ignore,status", [("false", 500), ("true", 201)])
def test_send_failure_ignore_error_and_invalid_guid(monkeypatch, ignore, status) -> None:
    store = BrokerStore()
    policy = f'<policies><inbound><send-service-bus-message queue-name="missing" ignore-error="{ignore}"><payload>hello</payload></send-service-bus-message><return-response><set-status code="201" /></return-response></inbound><backend /><outbound /><on-error /></policies>'
    with _gateway(store, policy, monkeypatch) as client:
        assert client.post("/messages").status_code == status
    invalid = policy.replace('queue-name="missing"', 'queue-name="missing" message-id="invalid"')
    with _gateway(store, invalid, monkeypatch) as client:
        assert client.post("/messages").status_code == 500


def test_eventhub_request_response_logs_preserve_original_bodies(monkeypatch) -> None:
    store = BrokerStore()
    store.create("topics", "logs")
    store.create("topics", "logs", "analytics")
    policy = '<policies><inbound><log-to-eventhub id="log-request" logger-id="logs" partition-id="0">@(context.Request.Body.As&lt;string&gt;(preserveContent: true))</log-to-eventhub></inbound><backend><forward-request /></backend><outbound><log-to-eventhub logger-id="logs" partition-id="1">@(context.Response.Body.As&lt;string&gt;(preserveContent: true))</log-to-eventhub></outbound><on-error /></policies>'
    with _gateway(store, policy, monkeypatch) as client:
        assert client.post("/messages", content="unchanged").text == "unchanged"
    request = store.consume("topics", "logs", "analytics")
    response = store.consume("topics", "logs", "analytics")
    assert request["payload"] == response["payload"] == "unchanged"
    assert (request["partition_id"], response["partition_id"]) == ("0", "1")


@pytest.mark.parametrize(
    "granted,client_id,status", [(["sender-user"], "sender-user", 200), (["system-assigned"], "ungranted-user", 500)]
)
def test_eventhub_logger_uses_configured_identity_for_sender_grant(monkeypatch, granted, client_id, status) -> None:
    store = BrokerStore()
    store.create("topics", "logs")
    store.create("topics", "logs", "analytics")
    policy = '<policies><inbound><log-to-eventhub logger-id="logs">hello</log-to-eventhub></inbound><backend><forward-request /></backend><outbound /><on-error /></policies>'
    with _gateway(store, policy, monkeypatch, allowed_client_ids=granted, logger_client_id=client_id) as client:
        assert client.post("/messages", content="payload").status_code == status
    message = store.consume("topics", "logs", "analytics")
    if status == 200:
        assert message["payload"] == "hello"
    else:
        assert message is None


def test_http_request_log_preserves_repeated_query_parameters_and_original_body(monkeypatch) -> None:
    store = BrokerStore()
    store.create("topics", "logs")
    store.create("topics", "logs", "analytics")
    policy = '<policies><inbound><log-to-eventhub logger-id="logs">@(context.Request.ToHttpMessage(1024))</log-to-eventhub></inbound><backend><forward-request /></backend><outbound /><on-error /></policies>'
    with _gateway(store, policy, monkeypatch) as client:
        response = client.post(
            "/messages?hello=world&tag=one&tag=two",
            content="body-survives",
            headers={"Authorization": "Bearer private-demo", "X-Example": "retained"},
        )
        assert response.status_code == 200 and response.text == "body-survives"
    message = store.consume("topics", "logs", "analytics")["payload"]
    assert message.startswith("POST /messages?hello=world&tag=one&tag=two HTTP/1.1\r\n")
    assert message.endswith("\r\n\r\nbody-survives")
    assert "retained" in message
    assert "private-demo" not in message


def test_one_way_notification_does_not_wait_or_change_response_on_transport_failure():
    import asyncio

    from app.policy import PolicyRequest, PolicyRuntime, apply_outbound_async, parse_policies_xml

    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()

        async def receiver(request):
            assert request.content == b"notification"
            started.set()
            await release.wait()
            raise httpx.ConnectError("Local receiver unavailable", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(receiver)) as client:
            runtime = PolicyRuntime(http_client=client)
            req = PolicyRequest("POST", "/api", {}, {}, {}, body=b"original", response_body=b"backend result")
            document = parse_policies_xml(
                '<policies><outbound><send-one-way-request mode="new">'
                "<set-url>http://receiver/notify</set-url><set-method>POST</set-method>"
                "<set-body>notification</set-body></send-one-way-request></outbound></policies>"
            )
            # The receiver cannot complete until after the policy has returned.
            assert await asyncio.wait_for(apply_outbound_async([document], req, runtime), 1) is None
            await asyncio.wait_for(started.wait(), 1)
            assert req.body == b"original" and req.response_body == b"backend result"
            tasks = list(runtime.background_tasks)
            assert len(tasks) == 1 and not tasks[0].done()
            release.set()
            await asyncio.gather(*tasks)
            assert req.response_body == b"backend result"

    asyncio.run(scenario())
