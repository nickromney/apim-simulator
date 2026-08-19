import pytest
from fastapi import HTTPException

from app.config import GatewayConfig, ProductConfig, ProductState, RouteConfig, SubscriptionIdentity
from app.request_pipeline import enforce_product_grant, enforce_route_authz, extract_roles, extract_scopes
from app.security import AuthContext
from app.urls import http_url


def _auth(*, products: list[str] | None = None, subscription: bool = True) -> AuthContext:
    identity = SubscriptionIdentity(id="demo", name="Demo") if subscription else None
    return AuthContext(claims={"sub": "user"}, subscription=identity, subscription_products=products or [])


def test_enforce_product_grant_returns_empty_when_route_has_no_products() -> None:
    cfg = GatewayConfig()
    route = RouteConfig(name="r1", path_prefix="/api", upstream_base_url=http_url("upstream"))
    assert enforce_product_grant(cfg, route, _auth(), subscription_is_bypassed=False) == ""


def test_enforce_product_grant_rejects_unpublished_product() -> None:
    cfg = GatewayConfig(products={"starter": ProductConfig(name="starter", state=ProductState.NotPublished)})
    route = RouteConfig(name="r1", path_prefix="/api", upstream_base_url=http_url("upstream"), products=["starter"])
    with pytest.raises(HTTPException) as exc:
        enforce_product_grant(cfg, route, _auth(products=["starter"]), subscription_is_bypassed=False)
    assert exc.value.status_code == 403
    assert exc.value.detail == "Product is not published"


def test_enforce_product_grant_requires_subscription_key() -> None:
    cfg = GatewayConfig(products={"starter": ProductConfig(name="starter")})
    route = RouteConfig(name="r1", path_prefix="/api", upstream_base_url=http_url("upstream"), products=["starter"])
    with pytest.raises(HTTPException) as exc:
        enforce_product_grant(cfg, route, _auth(subscription=False), subscription_is_bypassed=False)
    assert exc.value.status_code == 401


def test_enforce_product_grant_picks_first_published_granted_product() -> None:
    cfg = GatewayConfig(
        products={
            "closed": ProductConfig(name="closed", state=ProductState.NotPublished),
            "starter": ProductConfig(name="starter"),
        }
    )
    route = RouteConfig(
        name="r1",
        path_prefix="/api",
        upstream_base_url=http_url("upstream"),
        products=["closed", "starter"],
    )
    assert enforce_product_grant(cfg, route, _auth(products=["starter"]), subscription_is_bypassed=False) == "starter"


def test_extract_scopes_and_roles() -> None:
    assert extract_scopes({"scope": "read write"}) == {"read", "write"}
    assert extract_roles({"roles": ["admin"], "realm_access": {"roles": ["ops"]}}) == {"admin", "ops"}


def test_enforce_route_authz_requires_scope() -> None:
    from app.config import RouteAuthzConfig

    route = RouteConfig(
        name="r1",
        path_prefix="/api",
        upstream_base_url=http_url("upstream"),
        authz=RouteAuthzConfig(required_scopes=["orders.read"]),
    )
    with pytest.raises(HTTPException) as exc:
        enforce_route_authz(route, {"scope": "other"})
    assert exc.value.status_code == 403
