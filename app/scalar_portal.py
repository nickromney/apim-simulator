"""Locally served Scalar browser assets and consumer-scoped API contracts."""

from __future__ import annotations

import gzip
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.responses import HTMLResponse

from app.config import ApiConfig, GatewayConfig
from app.named_values import mask_secret_data
from app.openapi_export import build_openapi
from app.portal import product_visible, user_group_ids

ASSET_DIR = Path(__file__).parent / "static" / "scalar"


def portal_openapi(cfg: GatewayConfig, user_id: str, api_id: str) -> dict[str, Any]:
    """Expose only contracts reachable through this user's visible products."""
    api = cfg.apis.get(api_id)
    groups = user_group_ids(cfg, user_id)
    if api is None or not any(
        product_id in cfg.products and product_visible(cfg.products[product_id], groups) for product_id in api.products
    ):
        raise HTTPException(status_code=404, detail="API not found")
    try:
        document = build_openapi(api, version_set=cfg.api_version_sets.get(api.api_version_set or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Cannot export API: {exc}") from exc
    _subscription_security(document, cfg, api)
    return mask_secret_data(document, cfg)


def _subscription_security(document: dict[str, Any], cfg: GatewayConfig, api: ApiConfig) -> None:
    # Keys belong to the user's subscription response, never to downloadable contracts.
    schemes = {}
    for operation in api.operations.values():
        header_names = (
            operation.subscription_header_names or api.subscription_header_names or cfg.subscription.header_names
        )
        if not header_names:
            continue
        name = header_names[0]
        scheme = "subscription_" + str(len(schemes))
        scheme = next((key for key, value in schemes.items() if value["name"] == name), scheme)
        schemes[scheme] = {"type": "apiKey", "in": "header", "name": name}
        path = "/" + operation.url_template.split("?", 1)[0].lstrip("/")
        products = operation.products if operation.products is not None else api.products
        open_access = any(
            (product := cfg.products.get(product_id)) is not None and not product.require_subscription
            for product_id in products
        )
        security = [{scheme: []}]
        if not cfg.subscription.required or open_access:
            security.append({})
        document["paths"][path][operation.method.lower()]["security"] = security
    document["components"]["securitySchemes"] = schemes


def scalar_asset(request: Request, asset: str) -> Response:
    # Fixed allowlist: never interpret a client-supplied filesystem path.
    if asset == "scalar.js":
        content = (ASSET_DIR / "scalar.js.gz").read_bytes()
        headers = {"Cache-Control": "no-cache", "Vary": "Accept-Encoding", "X-Content-Type-Options": "nosniff"}
        if "gzip" in request.headers.get("accept-encoding", ""):
            headers["Content-Encoding"] = "gzip"
        else:
            content = gzip.decompress(content)
        return Response(content, media_type="text/javascript", headers=headers)
    if asset in {"reference.js", "theme.js"}:
        return Response(
            (ASSET_DIR / asset).read_bytes(), media_type="text/javascript", headers={"Cache-Control": "no-cache"}
        )
    raise HTTPException(status_code=404, detail="Asset not found")


def scalar_reference_page() -> HTMLResponse:
    return HTMLResponse(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Local API reference</title></head><body><div id="reference"></div>'
        '<script src="/apim/portal/assets/scalar.js"></script>'
        '<script src="/apim/portal/assets/reference.js"></script></body></html>',
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; "
            "object-src 'none'; base-uri 'none'",
            "Referrer-Policy": "no-referrer",
        },
    )
