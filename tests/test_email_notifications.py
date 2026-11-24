from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import (
    EmailSettingsConfig,
    GatewayConfig,
    NotificationRecipientConfig,
    PortalConfig,
    ProductConfig,
    SubscriptionConfig,
    TenantAccessConfig,
    UserConfig,
)
from app.email_notifications import NotificationRuntime
from app.main import create_app

pytestmark = pytest.mark.contract("PORTAL-EMAIL-NOTIFICATIONS")


def _config(**overrides: object) -> GatewayConfig:
    values: dict[str, object] = {
        "allow_anonymous": True,
        "portal": PortalConfig(enabled=True),
        "tenant_access": TenantAccessConfig(enabled=True, primary_key="tenant-key"),
        "users": {
            "dev-1": UserConfig(
                id="dev-1",
                email="developer@example.test",
                first_name="Ada",
                last_name="Lovelace",
                state="active",
            )
        },
        "products": {"gated": ProductConfig(name="Gated Product", approval_required=True)},
        "subscription": SubscriptionConfig(required=False),
        "email_settings": EmailSettingsConfig(organization_name="Example APIs"),
    }
    values.update(overrides)
    return GatewayConfig.model_validate(values)


def _client(config: GatewayConfig) -> TestClient:
    return TestClient(
        create_app(
            config=config,
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"ok": True}))
            ),
        )
    )


def _management_headers() -> dict[str, str]:
    return {"X-Apim-Tenant-Key": "tenant-key"}


def test_default_templates_substitute_parameters_and_reset_after_update() -> None:
    with _client(_config()) as client:
        default = client.get(
            "/apim/management/email-templates/PurchaseDeveloperNotificationMessage", headers=_management_headers()
        )
        assert default.status_code == 200
        assert default.json()["subject"] == "Your subscription is activated"
        assert "$SubId" in default.json()["body"]

        updated = client.put(
            "/apim/management/email-templates/PurchaseDeveloperNotificationMessage",
            headers=_management_headers(),
            json={"subject": "Hello $DevFirstName", "body": "<p>$ProdName</p>"},
        )
        assert updated.status_code == 200
        assert updated.json()["customized"] is True

        reset = client.delete(
            "/apim/management/email-templates/PurchaseDeveloperNotificationMessage", headers=_management_headers()
        )
        assert reset.status_code == 200
        assert reset.json()["customized"] is False
        assert reset.json()["subject"] == "Your subscription is activated"


def test_notification_recipient_emails_can_be_added_and_removed() -> None:
    path = "/apim/management/notifications/BCC/recipient-emails"
    with _client(_config()) as client:
        added = client.post(path, headers=_management_headers(), json={"email": "audit@example.test"})
        assert added.status_code == 200
        assert added.json()["emails"] == ["audit@example.test"]

        listed = client.get(path, headers=_management_headers())
        assert listed.json() == {"notification": "BCC", "emails": ["audit@example.test"]}

        removed = client.delete(f"{path}/audit@example.test", headers=_management_headers())
        assert removed.status_code == 200
        assert removed.json()["emails"] == []


def test_approval_required_request_and_approval_publish_expected_messages() -> None:
    recipients = {
        "RequestPublisherNotificationMessage": NotificationRecipientConfig(emails=["publisher@example.test"]),
        "PurchasePublisherNotificationMessage": NotificationRecipientConfig(emails=["publisher@example.test"]),
    }
    with _client(_config(notification_recipients=recipients)) as client:
        created = client.post(
            "/apim/portal/subscriptions",
            headers={"X-Apim-Portal-User": "dev-1"},
            json={"product_id": "gated"},
        )
        assert created.status_code == 201
        messages = client.get("/apim/management/outbox", headers=_management_headers()).json()["messages"]
        assert {message["notification"] for message in messages} == {
            "RequestPublisherNotificationMessage",
            "RequestDeveloperNotificationMessage",
        }
        assert all("sub-dev-1-gated-primary" not in str(message) for message in messages)

        approved = client.patch(
            f"/apim/management/subscriptions/{created.json()['id']}",
            headers=_management_headers(),
            json={"state": "active"},
        )
        assert approved.status_code == 200
        messages = client.get("/apim/management/outbox", headers=_management_headers()).json()["messages"]
        assert {message["notification"] for message in messages} == {
            "RequestPublisherNotificationMessage",
            "RequestDeveloperNotificationMessage",
            "PurchasePublisherNotificationMessage",
            "PurchaseDeveloperNotificationMessage",
        }


def test_rejection_and_bcc_use_developer_email() -> None:
    recipients = {"BCC": NotificationRecipientConfig(emails=["audit@example.test"])}
    with _client(_config(notification_recipients=recipients)) as client:
        created = client.post(
            "/apim/portal/subscriptions",
            headers={"X-Apim-Portal-User": "dev-1"},
            json={"product_id": "gated"},
        )
        rejected = client.patch(
            f"/apim/management/subscriptions/{created.json()['id']}",
            headers=_management_headers(),
            json={"state": "rejected"},
        )
        assert rejected.status_code == 200
        messages = client.get("/apim/management/outbox", headers=_management_headers()).json()["messages"]
        message = next(item for item in messages if item["notification"] == "RejectDeveloperNotificationMessage")
        assert message["to"] == ["developer@example.test"]
        assert message["bcc"] == ["audit@example.test"]
        assert "sub-dev-1-gated-primary" not in str(message)


def test_active_subscription_publishes_activation_and_outbox_can_be_cleared() -> None:
    with _client(_config(products={"open": ProductConfig(name="Open")})) as client:
        created = client.post(
            "/apim/portal/subscriptions",
            headers={"X-Apim-Portal-User": "dev-1"},
            json={"product_id": "open"},
        )
        assert created.status_code == 201
        messages = client.get("/apim/management/outbox", headers=_management_headers()).json()["messages"]
        assert messages[0]["notification"] == "PurchaseDeveloperNotificationMessage"
        assert messages[0]["to"] == ["developer@example.test"]
        assert "dev-1-open" in messages[0]["body"]
        assert "Open" in messages[0]["body"]

        cleared = client.delete("/apim/management/outbox", headers=_management_headers())
        assert cleared.json()["cleared"] == 1
        assert client.get("/apim/management/outbox", headers=_management_headers()).json()["messages"] == []


def test_outbox_is_bounded_to_two_hundred_newest_messages() -> None:
    runtime = NotificationRuntime()
    config = _config()
    for index in range(201):
        runtime._publish(
            config,
            notification=f"event-{index}",
            recipients=["developer@example.test"],
            subject=str(index),
            body="body",
        )
    messages = runtime.list_messages()
    assert len(messages) == 200
    assert messages[0]["subject"] == "200"
    assert messages[-1]["subject"] == "1"
    runtime.clear()
    assert runtime.list_messages() == []


def test_notification_management_endpoints_require_tenant_key() -> None:
    with _client(_config()) as client:
        for path in (
            "/apim/management/notifications",
            "/apim/management/email-templates",
            "/apim/management/email-settings",
            "/apim/management/outbox",
        ):
            assert client.get(path).status_code == 403
