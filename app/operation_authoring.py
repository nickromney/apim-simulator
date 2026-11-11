"""Targeted operation edits that never round-trip redacted projections."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from xml.etree import ElementTree as ET

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.config import (
    GatewayConfig,
    OperationConfig,
    OperationExampleConfig,
    OperationRepresentationConfig,
    OperationResponseMetadataConfig,
)
from app.effective_policy import EMPTY_POLICY_XML
from app.named_values import mask_secret_data
from app.resource_projection import project_operation
from app.security import require_tenant_access

if TYPE_CHECKING:
    from app.management_service import ManagementService


class ResponseExampleEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status_code: int = Field(ge=100, le=599, strict=True)
    content_type: str = Field(min_length=1, max_length=255, pattern=r"^\S[^\r\n]*$")
    example_name: str | None = Field(default=None, min_length=1)
    value: Any


class MockEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    status_code: int = Field(ge=100, le=599, strict=True)
    content_type: str = Field(min_length=1, max_length=255, pattern=r"^\S[^\r\n]*$")


def _response_representation(operation: OperationConfig, body: ResponseExampleEdit) -> OperationRepresentationConfig:
    response = next((r for r in operation.responses if r.status_code == body.status_code), None)
    if response is None:
        response = OperationResponseMetadataConfig(status_code=body.status_code)
        operation.responses.append(response)
    representation = next(
        (r for r in response.representations if r.content_type.lower() == body.content_type.lower()), None
    )
    if representation is None:
        representation = OperationRepresentationConfig(content_type=body.content_type)
        response.representations.append(representation)
    return representation


def _edit_response_example(cfg: GatewayConfig, operation: OperationConfig, body: ResponseExampleEdit) -> None:
    if body.value is None:
        raise HTTPException(400, "Mock response examples must be non-null")
    representation = _response_representation(operation, body)
    if body.example_name is not None:
        example = next((e for e in representation.examples if e.name == body.example_name), None)
        if example is None:
            raise HTTPException(409, "Example no longer exists")
        masked = mask_secret_data(example.value, cfg)
        if masked != example.value and body.value == masked:
            raise HTTPException(409, "The example contains redacted values; provide a replacement value")
        example.value = body.value
        example.value_format = "json" if "json" in body.content_type.lower() else "auto"
    else:
        if any(e.value is not None for e in representation.examples):
            raise HTTPException(409, "This response already has an example; edit its first non-null example")
        names = {e.name for e in representation.examples}
        name, number = "example", 2
        while name in names:
            name, number = f"example-{number}", number + 1
        representation.examples.append(
            OperationExampleConfig(
                name=name, value=body.value, value_format="json" if "json" in body.content_type.lower() else "auto"
            )
        )


def _edit_mock_response(
    cfg: GatewayConfig, manager: ManagementService, operation: OperationConfig, body: MockEdit
) -> None:
    original_xml = operation.policies_xml or EMPTY_POLICY_XML
    manager.validate_policy_xml(cfg, original_xml)
    try:
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        root = ET.fromstring(original_xml, parser=parser)
    except ET.ParseError as exc:
        raise HTTPException(400, "Invalid operation policy XML") from exc
    inbound = root.find("inbound")
    if root.tag != "policies" or inbound is None:
        raise HTTPException(409, "Operation policy has no inbound section; edit it in Design")
    mocks = list(root.iter("mock-response"))
    direct = [item for item in inbound if item.tag == "mock-response"]
    if len(mocks) != len(direct) or len(direct) > 1:
        raise HTTPException(409, "Mocking is conditional or appears multiple times; edit it in Design")
    if body.enabled:
        preceding = list(inbound)[: list(inbound).index(direct[0])] if direct else list(inbound)
        if any(item.tag == "return-response" for item in preceding):
            raise HTTPException(409, "An earlier return-response makes mocking unreachable; edit the policy in Design")
        mock = direct[0] if direct else ET.SubElement(inbound, "mock-response")
        mock.set("status-code", str(body.status_code))
        mock.set("content-type", body.content_type)
    elif direct:
        inbound.remove(direct[0])
    xml = ET.tostring(root, encoding="unicode")
    manager.validate_policy_xml(cfg, xml)
    operation.policies_xml = xml


def build_operation_authoring_router(*, require_management_plane: Callable[[], ManagementService]) -> APIRouter:
    router = APIRouter()

    def target(
        request: Request, api_id: str, operation_id: str
    ) -> tuple[GatewayConfig, ManagementService, OperationConfig]:
        require_tenant_access(request)
        cfg = request.app.state.gateway_config.model_copy(deep=True)
        manager = require_management_plane()
        manager.require_api_authoring_mode(cfg)
        api = cfg.apis.get(api_id)
        operation = api.operations.get(operation_id) if api else None
        if operation is None:
            raise HTTPException(404, "Operation not found")
        return cfg, manager, operation

    def commit(cfg: GatewayConfig, manager: ManagementService, api_id: str, operation_id: str) -> dict[str, Any]:
        updated = manager.persist_or_apply_config(cfg)
        return mask_secret_data(
            project_operation(updated, api_id, operation_id, updated.apis[api_id].operations[operation_id]), updated
        )

    @router.put("/apim/management/apis/{api_id}/operations/{operation_id}/response-example")
    async def response_example(api_id: str, operation_id: str, body: ResponseExampleEdit, request: Request):
        cfg, manager, operation = target(request, api_id, operation_id)
        _edit_response_example(cfg, operation, body)
        return commit(cfg, manager, api_id, operation_id)

    @router.put("/apim/management/apis/{api_id}/operations/{operation_id}/mock-response")
    async def mock_response(api_id: str, operation_id: str, body: MockEdit, request: Request):
        cfg, manager, operation = target(request, api_id, operation_id)
        _edit_mock_response(cfg, manager, operation, body)
        return commit(cfg, manager, api_id, operation_id)

    return router
