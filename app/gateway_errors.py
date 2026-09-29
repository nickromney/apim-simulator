"""Errors returned by the public gateway path."""

from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import GatewayConfig, RouteConfig

# The troubleshooting article shows APIM's caller-visible response with "an
# API"; the error-handling table currently says "this API". Match the observed
# response documented here: https://learn.microsoft.com/en-us/troubleshoot/azure/api-mgmt/availability/unauthorized-errors-invoke-apis
MISSING_SUBSCRIPTION_KEY_MESSAGE = "Access denied due to missing subscription key. Make sure to include subscription key when making requests to an API."
INVALID_SUBSCRIPTION_KEY_MESSAGE = (
    "Access denied due to invalid subscription key. Make sure to provide a valid key for an active subscription."
)
DEFAULT_SUBSCRIPTION_HEADER_NAME = "Ocp-Apim-Subscription-Key"


class GatewayError(HTTPException):
    """An error raised while serving an API caller through the gateway."""

    @classmethod
    def from_http_exception(cls, exc: HTTPException) -> GatewayError:
        return cls(status_code=exc.status_code, message=str(exc.detail), headers=exc.headers)

    def __init__(self, *, status_code: int, message: str, headers: dict[str, str] | None = None):
        super().__init__(status_code=status_code, detail=message, headers=headers)


async def gateway_error_handler(_: Request, exc: GatewayError) -> JSONResponse:
    """Render the APIM gateway envelope without affecting other routers."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"statusCode": exc.status_code, "message": str(exc.detail)},
        headers=exc.headers,
    )


def subscription_key_headers(request: Request, config: GatewayConfig, route: RouteConfig | None) -> dict[str, str]:
    """Build APIM's subscription-key challenge for one API route."""
    header_names = (
        route.subscription_header_names
        if route and route.subscription_header_names
        else config.subscription.header_names
    )
    header_name = header_names[0] if header_names else DEFAULT_SUBSCRIPTION_HEADER_NAME
    path = route.path_prefix.rstrip("/") if route and route.path_prefix else request.url.path
    realm_path = path or "/"
    realm = f"{request.url.scheme}://{request.url.netloc}{realm_path}"
    return {"WWW-Authenticate": f'AzureApiManagementKey realm="{realm}",name="{header_name}",type="header"'}


def subscription_key_error(
    request: Request | None,
    config: GatewayConfig,
    route: RouteConfig | None,
    *,
    missing: bool,
) -> GatewayError:
    """Create APIM's missing/invalid subscription-key error."""
    message = MISSING_SUBSCRIPTION_KEY_MESSAGE if missing else INVALID_SUBSCRIPTION_KEY_MESSAGE
    headers = subscription_key_headers(request, config, route) if request is not None else None
    return GatewayError(status_code=401, message=message, headers=headers)
