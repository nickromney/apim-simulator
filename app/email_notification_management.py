"""Tenant-key-protected management routes for local email notifications."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from app.config import EmailSettingsConfig, GatewayConfig, NotificationRecipientConfig
from app.email_notifications import (
    DEFAULT_TEMPLATES,
    DEVELOPER_TEMPLATE_NAMES,
    PUBLISHER_NOTIFICATION_NAMES,
    default_template,
    effective_template,
)
from app.management_service import ManagementService
from app.security import require_tenant_access


class RecipientEmailBody(BaseModel):
    email: str


class EmailTemplateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str
    body: str


class EmailSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    administrator_email: str | None = None
    organization_name: str | None = None
    originating_email: str | None = None
    developer_portal_url: str | None = None


def _notification_names(cfg: GatewayConfig) -> list[str]:
    return list(dict.fromkeys([*PUBLISHER_NOTIFICATION_NAMES, *cfg.notification_recipients]))


def _template_names(cfg: GatewayConfig) -> list[str]:
    return list(dict.fromkeys([*DEVELOPER_TEMPLATE_NAMES, *cfg.email_templates]))


def _recipient_payload(cfg: GatewayConfig, name: str) -> dict[str, Any]:
    recipients = cfg.notification_recipients.get(name, NotificationRecipientConfig())
    return {"name": name, "emails": list(recipients.emails), "users": list(recipients.users)}


def _template_payload(cfg: GatewayConfig, name: str) -> dict[str, Any]:
    template = effective_template(cfg, name)
    default = default_template(name) if name in DEFAULT_TEMPLATES else template
    return {
        "name": name,
        "subject": template.subject,
        "body": template.body,
        "customized": name in cfg.email_templates,
        "default": default.model_dump(mode="json"),
    }


def _recipient(cfg: GatewayConfig, name: str, *, create: bool = False) -> NotificationRecipientConfig:
    if name not in _notification_names(cfg):
        raise HTTPException(status_code=404, detail="Notification not found")
    recipients = cfg.notification_recipients.get(name)
    if recipients is None and create:
        recipients = NotificationRecipientConfig()
        cfg.notification_recipients[name] = recipients
    return recipients or NotificationRecipientConfig()


def _template(cfg: GatewayConfig, name: str) -> None:
    if name not in _template_names(cfg):
        raise HTTPException(status_code=404, detail="Email template not found")


def build_email_notification_router(  # noqa: C901 - one branch per route; see docs/complexity.md
    *, require_management_plane: Callable[[], ManagementService]
) -> APIRouter:
    router = APIRouter()

    @router.get("/apim/management/notifications")
    async def list_notifications(request: Request) -> list[dict[str, Any]]:
        require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        return [_recipient_payload(cfg, name) for name in _notification_names(cfg)]

    @router.get("/apim/management/notifications/{notification_name}")
    async def get_notification(notification_name: str, request: Request) -> dict[str, Any]:
        require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        _recipient(cfg, notification_name)
        return _recipient_payload(cfg, notification_name)

    @router.get("/apim/management/notifications/{notification_name}/recipient-emails")
    async def list_recipient_emails(notification_name: str, request: Request) -> dict[str, Any]:
        require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        _recipient(cfg, notification_name)
        return {"notification": notification_name, "emails": _recipient_payload(cfg, notification_name)["emails"]}

    @router.put("/apim/management/notifications/{notification_name}/recipient-emails/{email}")
    async def add_recipient_email(notification_name: str, email: str, request: Request) -> dict[str, Any]:
        require_tenant_access(request)
        cfg = request.app.state.gateway_config.model_copy(deep=True)
        recipients = _recipient(cfg, notification_name, create=True)
        if email not in recipients.emails:
            recipients.emails.append(email)
        updated = require_management_plane().persist_or_apply_config(cfg)
        return _recipient_payload(updated, notification_name)

    @router.post("/apim/management/notifications/{notification_name}/recipient-emails")
    async def add_recipient_email_body(
        notification_name: str, request: Request, body: RecipientEmailBody
    ) -> dict[str, Any]:
        return await add_recipient_email(notification_name, body.email, request)

    @router.delete("/apim/management/notifications/{notification_name}/recipient-emails/{email}")
    async def remove_recipient_email(notification_name: str, email: str, request: Request) -> dict[str, Any]:
        require_tenant_access(request)
        cfg = request.app.state.gateway_config.model_copy(deep=True)
        recipients = _recipient(cfg, notification_name)
        if email not in recipients.emails:
            raise HTTPException(status_code=404, detail="Notification recipient email not found")
        recipients.emails.remove(email)
        updated = require_management_plane().persist_or_apply_config(cfg)
        return _recipient_payload(updated, notification_name)

    @router.get("/apim/management/email-templates")
    async def list_email_templates(request: Request) -> list[dict[str, Any]]:
        require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        return [_template_payload(cfg, name) for name in _template_names(cfg)]

    @router.get("/apim/management/email-templates/{template_name}")
    async def get_email_template(template_name: str, request: Request) -> dict[str, Any]:
        require_tenant_access(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        _template(cfg, template_name)
        return _template_payload(cfg, template_name)

    @router.put("/apim/management/email-templates/{template_name}")
    async def update_email_template(template_name: str, request: Request, body: EmailTemplateUpdate) -> dict[str, Any]:
        require_tenant_access(request)
        cfg = request.app.state.gateway_config.model_copy(deep=True)
        _template(cfg, template_name)
        from app.config import EmailTemplateConfig

        cfg.email_templates[template_name] = EmailTemplateConfig(subject=body.subject, body=body.body)
        updated = require_management_plane().persist_or_apply_config(cfg)
        return _template_payload(updated, template_name)

    @router.delete("/apim/management/email-templates/{template_name}")
    async def reset_email_template(template_name: str, request: Request) -> dict[str, Any]:
        require_tenant_access(request)
        cfg = request.app.state.gateway_config.model_copy(deep=True)
        _template(cfg, template_name)
        cfg.email_templates.pop(template_name, None)
        updated = require_management_plane().persist_or_apply_config(cfg)
        return _template_payload(updated, template_name)

    @router.get("/apim/management/email-settings")
    async def get_email_settings(request: Request) -> dict[str, Any]:
        require_tenant_access(request)
        return request.app.state.gateway_config.email_settings.model_dump(mode="json")

    @router.put("/apim/management/email-settings")
    async def update_email_settings(request: Request, body: EmailSettingsUpdate) -> dict[str, Any]:
        require_tenant_access(request)
        cfg = request.app.state.gateway_config.model_copy(deep=True)
        values = cfg.email_settings.model_dump(mode="python")
        values.update(body.model_dump(exclude_unset=True))
        cfg.email_settings = EmailSettingsConfig.model_validate(values)
        updated = require_management_plane().persist_or_apply_config(cfg)
        return updated.email_settings.model_dump(mode="json")

    @router.get("/apim/management/outbox")
    async def list_outbox(request: Request, limit: int = Query(default=200, ge=1, le=200)) -> dict[str, Any]:
        require_tenant_access(request)
        messages = request.app.state.notification_runtime.list_messages()[:limit]
        return {"messages": messages, "count": len(messages)}

    @router.delete("/apim/management/outbox")
    async def clear_outbox(request: Request) -> dict[str, Any]:
        require_tenant_access(request)
        runtime = request.app.state.notification_runtime
        count = len(runtime.list_messages())
        runtime.clear()
        return {"cleared": count}

    return router
