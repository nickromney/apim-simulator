"""Local APIM-style email notifications.

Azure APIM exposes publisher notifications, developer email templates, and
general email settings:

* https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-configure-notifications
* https://learn.microsoft.com/en-us/rest/api/apimanagement/notification
* https://learn.microsoft.com/en-us/rest/api/apimanagement/notification-recipient-email
* https://learn.microsoft.com/en-us/rest/api/apimanagement/email-template

The simulator renders messages into an in-memory outbox instead of sending
them through SMTP.  Subscription keys are deliberately never included in the
rendering context.
"""

from __future__ import annotations

import re
from collections import deque
from datetime import UTC, datetime
from html import escape
from typing import Any
from uuid import uuid4

from app.config import (
    EmailTemplateConfig,
    GatewayConfig,
    NotificationRecipientConfig,
    ProductConfig,
    Subscription,
    SubscriptionState,
    UserConfig,
)

PUBLISHER_NOTIFICATION_NAMES = (
    "RequestPublisherNotificationMessage",
    "PurchasePublisherNotificationMessage",
    "NewApplicationNotificationMessage",
    "BCC",
    "NewIssuePublisherNotificationMessage",
    "AccountClosedPublisher",
    "QuotaLimitApproachingPublisherNotificationMessage",
)

DEVELOPER_TEMPLATE_NAMES = (
    "ApplicationApprovedNotificationMessage",
    "AccountClosedDeveloper",
    "QuotaLimitApproachingDeveloperNotificationMessage",
    "NewDeveloperNotificationMessage",
    "EmailChangeIdentityDefault",
    "InviteUserNotificationMessage",
    "NewCommentNotificationMessage",
    "ConfirmSignUpIdentityDefault",
    "NewIssueNotificationMessage",
    "PurchaseDeveloperNotificationMessage",
    "PasswordResetIdentityDefault",
    "PasswordResetByAdminNotificationMessage",
    "RejectDeveloperNotificationMessage",
    "RequestDeveloperNotificationMessage",
)

DEFAULT_TEMPLATES = {
    "ApplicationApprovedNotificationMessage": (
        "Your application was approved",
        "<p>Hello $DevFirstName, your application was approved by $OrganizationName.</p>",
    ),
    "AccountClosedDeveloper": (
        "Your developer account was closed",
        "<p>Hello $DevFirstName, your account at $OrganizationName has been closed.</p>",
    ),
    "QuotaLimitApproachingDeveloperNotificationMessage": (
        "Your subscription is approaching its quota limit",
        "<p>Your subscription $SubId is approaching its quota limit at $OrganizationName.</p>",
    ),
    "NewDeveloperNotificationMessage": (
        "Welcome to $OrganizationName",
        "<p>Welcome $DevFirstName $DevLastName to $OrganizationName.</p>",
    ),
    "EmailChangeIdentityDefault": (
        "Confirm your new email address",
        "<p>Hello $DevFirstName, confirm your email address at $OrganizationName.</p>",
    ),
    "InviteUserNotificationMessage": (
        "You are invited to $OrganizationName",
        "<p>You have been invited to $OrganizationName. Visit $DevPortalUrl to continue.</p>",
    ),
    "NewCommentNotificationMessage": (
        "A new comment was posted",
        "<p>A new comment was posted on $OrganizationName.</p>",
    ),
    "ConfirmSignUpIdentityDefault": (
        "Confirm your developer account",
        "<p>Hello $DevFirstName, confirm your account at $OrganizationName.</p>",
    ),
    "NewIssueNotificationMessage": (
        "A new issue was reported",
        "<p>A new issue was reported by $DevFirstName $DevLastName.</p>",
    ),
    "PurchaseDeveloperNotificationMessage": (
        "Your subscription is activated",
        "<p>Your subscription $SubId for $ProdName is active.</p>",
    ),
    "PasswordResetIdentityDefault": (
        "Reset your password",
        "<p>Hello $DevFirstName, reset your password at $DevPortalUrl.</p>",
    ),
    "PasswordResetByAdminNotificationMessage": (
        "Your password was reset",
        "<p>An administrator reset your password at $OrganizationName.</p>",
    ),
    "RejectDeveloperNotificationMessage": (
        "Your subscription request was declined",
        "<p>Your request for $ProdName was declined by $OrganizationName.</p>",
    ),
    "RequestDeveloperNotificationMessage": (
        "Your subscription request was received",
        "<p>We received your request for $ProdName. We will notify you when it is reviewed.</p>",
    ),
}

_PARAMETER = re.compile(r"\$([A-Za-z][A-Za-z0-9]*)")


def default_template(name: str) -> EmailTemplateConfig:
    subject, body = DEFAULT_TEMPLATES[name]
    return EmailTemplateConfig(subject=subject, body=body)


def effective_template(cfg: GatewayConfig, name: str) -> EmailTemplateConfig:
    override = cfg.email_templates.get(name)
    return override.model_copy(deep=True) if override is not None else default_template(name)


def _recipient_emails(cfg: GatewayConfig, name: str) -> list[str]:
    configured = cfg.notification_recipients.get(name, NotificationRecipientConfig())
    emails = list(configured.emails)
    emails.extend(user.email for user_id in configured.users if (user := cfg.users.get(user_id)) and user.email)
    if name != "BCC" and cfg.email_settings.administrator_email:
        emails.append(cfg.email_settings.administrator_email)
    return _unique(emails)


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _developer_context(
    cfg: GatewayConfig, user: UserConfig, subscription: Subscription, product: ProductConfig
) -> dict[str, str]:
    return {
        "DevFirstName": user.first_name or (user.name or user.id).split(" ", 1)[0],
        "DevLastName": user.last_name or ((user.name or "").split(" ", 1)[1] if " " in (user.name or "") else ""),
        "DevEmail": user.email or "",
        "OrganizationName": cfg.email_settings.organization_name,
        "DevPortalUrl": cfg.email_settings.developer_portal_url,
        "ProdName": product.name,
        "SubId": subscription.id,
    }


def _render(value: str, context: dict[str, str]) -> str:
    return _PARAMETER.sub(lambda match: context.get(match.group(1), match.group(0)), value)


class NotificationRuntime:
    """Bounded process-local outbox used by the management API and tests."""

    def __init__(self, max_messages: int = 200) -> None:
        self._outbox: deque[dict[str, Any]] = deque(maxlen=max_messages)

    def list_messages(self) -> list[dict[str, Any]]:
        return [dict(message) for message in reversed(self._outbox)]

    def clear(self) -> None:
        self._outbox.clear()

    def _publish(
        self,
        cfg: GatewayConfig,
        *,
        notification: str,
        recipients: list[str],
        subject: str,
        body: str,
        bcc: list[str] | None = None,
    ) -> None:
        if not recipients and not bcc:
            return
        self._outbox.append(
            {
                "id": f"email-{uuid4()}",
                "notification": notification,
                "to": _unique(recipients),
                "bcc": _unique(bcc or []),
                "from": cfg.email_settings.originating_email,
                "subject": subject,
                "body": body,
                "created_at": datetime.now(UTC).isoformat(),
            }
        )

    def _publisher_message(
        self, cfg: GatewayConfig, notification: str, subscription: Subscription, product: ProductConfig
    ) -> None:
        recipients = _recipient_emails(cfg, notification)
        self._publish(
            cfg,
            notification=notification,
            recipients=recipients,
            subject=f"{cfg.email_settings.organization_name}: {notification}",
            body=(
                f"<p>Notification <strong>{escape(notification)}</strong> was raised for "
                f"subscription <strong>{escape(subscription.id)}</strong> and product "
                f"<strong>{escape(product.name)}</strong>.</p>"
            ),
        )

    def _developer_message(
        self,
        cfg: GatewayConfig,
        notification: str,
        user: UserConfig,
        subscription: Subscription,
        product: ProductConfig,
    ) -> None:
        template = effective_template(cfg, notification)
        context = _developer_context(cfg, user, subscription, product)
        self._publish(
            cfg,
            notification=notification,
            recipients=[user.email] if user.email else [],
            bcc=_recipient_emails(cfg, "BCC"),
            subject=_render(template.subject, context),
            body=_render(template.body, {key: escape(value) for key, value in context.items()}),
        )

    def subscription_created(self, cfg: GatewayConfig, subscription: Subscription) -> None:
        user_id = subscription.created_by.removeprefix("portal:") if subscription.created_by else ""
        user = cfg.users.get(user_id)
        product = cfg.products.get(subscription.products[0]) if subscription.products else None
        if user is None or product is None:
            return
        if subscription.state == SubscriptionState.Submitted:
            self._publisher_message(cfg, "RequestPublisherNotificationMessage", subscription, product)
            self._developer_message(cfg, "RequestDeveloperNotificationMessage", user, subscription, product)
        elif subscription.state == SubscriptionState.Active:
            self._publisher_message(cfg, "PurchasePublisherNotificationMessage", subscription, product)
            self._developer_message(cfg, "PurchaseDeveloperNotificationMessage", user, subscription, product)

    def subscription_state_changed(self, cfg: GatewayConfig, previous: Subscription, current: Subscription) -> None:
        if previous.state == current.state:
            return
        user_id = current.created_by.removeprefix("portal:") if current.created_by else ""
        user = cfg.users.get(user_id)
        product = cfg.products.get(current.products[0]) if current.products else None
        if user is None or product is None:
            return
        if current.state == SubscriptionState.Active and previous.state == SubscriptionState.Submitted:
            self._publisher_message(cfg, "PurchasePublisherNotificationMessage", current, product)
            self._developer_message(cfg, "PurchaseDeveloperNotificationMessage", user, current, product)
        elif current.state == SubscriptionState.Rejected:
            self._developer_message(cfg, "RejectDeveloperNotificationMessage", user, current, product)
