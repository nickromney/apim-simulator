import pytest
from fastapi import HTTPException, Request

from app.config import (
    GatewayConfig,
    ProductConfig,
    ProductState,
    RouteConfig,
    Subscription,
    SubscriptionConfig,
    SubscriptionIdentity,
    SubscriptionKeyPair,
)
from app.request_pipeline import enforce_product_grant, enforce_route_authz, extract_roles, extract_scopes
from app.security import AuthContext, authenticate_request
from app.urls import http_url


def _auth(*, products: list[str] | None = None, subscription: bool = True) -> AuthContext:
    identity = SubscriptionIdentity(id="demo", name="Demo") if subscription else None
    return AuthContext(claims={"sub": "user"}, subscription=identity, subscription_products=products or [])


def test_enforce_product_grant_returns_empty_when_route_has_no_products() -> None:
    cfg = GatewayConfig()
    route = RouteConfig(name="r1", path_prefix="/api", upstream_base_url=http_url("upstream"))
    assert enforce_product_grant(cfg, route, _auth(), subscription_is_bypassed=False) == ""


def test_enforce_product_grant_rejects_unpublished_product() -> None:
    """Product access errors keep their status while using the gateway envelope.

    https://learn.microsoft.com/en-us/azure/api-management/api-management-error-handling-policies
    """
    cfg = GatewayConfig(products={"starter": ProductConfig(name="starter", state=ProductState.NotPublished)})
    route = RouteConfig(name="r1", path_prefix="/api", upstream_base_url=http_url("upstream"), products=["starter"])
    with pytest.raises(HTTPException) as exc:
        enforce_product_grant(cfg, route, _auth(products=["starter"]), subscription_is_bypassed=False)
    assert exc.value.status_code == 403
    assert exc.value.detail == "Product is not published"


def test_enforce_product_grant_requires_subscription_key() -> None:
    """APIM reports a missing subscription key with its standard 401 message.

    https://learn.microsoft.com/en-us/troubleshoot/azure/api-mgmt/availability/unauthorized-errors-invoke-apis
    """
    cfg = GatewayConfig(products={"starter": ProductConfig(name="starter")})
    route = RouteConfig(name="r1", path_prefix="/api", upstream_base_url=http_url("upstream"), products=["starter"])
    with pytest.raises(HTTPException) as exc:
        enforce_product_grant(cfg, route, _auth(subscription=False), subscription_is_bypassed=False)
    assert exc.value.status_code == 401
    assert (
        exc.value.detail
        == "Access denied due to missing subscription key. Make sure to include subscription key when making requests to an API."
    )


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
    """Simulator-only route authorization keeps 403 and uses the gateway envelope.

    https://learn.microsoft.com/en-us/azure/api-management/api-management-error-handling-policies
    """
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


# --- subscription keys against open products -------------------------------

SUBSCRIPTIONS_DOC = "https://learn.microsoft.com/en-us/azure/api-management/api-management-subscriptions"


class _StubVerifier:
    issuer = "https://issuer.test"

    def decode(self, token: str) -> dict:
        return {"sub": "user", "iss": self.issuer}


def _request(headers: dict[str, str] | None = None, query: str = "") -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/x",
            "headers": raw,
            "query_string": query.encode(),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


def _sub_cfg(*, anonymous: bool, products: dict[str, ProductConfig], sub_products: list[str]) -> tuple:
    cfg = GatewayConfig(
        allow_anonymous=anonymous,
        products=products,
        subscription=SubscriptionConfig(
            required=True,
            subscriptions={
                "s1": Subscription(
                    id="s1",
                    name="S1",
                    products=sub_products,
                    keys=SubscriptionKeyPair(primary="good", secondary="good2"),
                )
            },
        ),
    )
    route = RouteConfig(name="r1", path_prefix="/api", upstream_base_url=http_url("upstream"), products=list(products))
    return cfg, route


def _authenticate(cfg, route, headers=None, query=""):
    return authenticate_request(_request(headers, query), cfg, {"v": _StubVerifier()}, route)


OPEN = {"open": ProductConfig(name="open", require_subscription=False)}


def test_open_product_ignores_invalid_key_when_anonymous_allowed() -> None:
    """An unaccepted key is ignored when an open product includes the API.

    https://learn.microsoft.com/en-us/azure/api-management/api-management-subscriptions
    """
    cfg, route = _sub_cfg(anonymous=True, products=OPEN, sub_products=["open"])
    auth = _authenticate(cfg, route, {"Ocp-Apim-Subscription-Key": "nope"})
    assert auth.subscription is None
    assert enforce_product_grant(cfg, route, auth, subscription_is_bypassed=False) == "open"


def test_open_product_does_not_require_key_with_bearer_token() -> None:
    """A bearer-authenticated call to an open product needs no subscription key.

    https://learn.microsoft.com/en-us/azure/api-management/api-management-subscriptions
    """
    cfg, route = _sub_cfg(anonymous=False, products=OPEN, sub_products=["open"])
    auth = _authenticate(cfg, route, {"Authorization": "Bearer abc"})
    assert auth.subscription is None
    assert enforce_product_grant(cfg, route, auth, subscription_is_bypassed=False) == "open"


def test_open_product_still_uses_a_valid_key_for_context() -> None:
    """A key that validates keeps its subscription context on an open product.

    https://learn.microsoft.com/en-us/azure/api-management/api-management-subscriptions
    """
    cfg, route = _sub_cfg(anonymous=True, products=OPEN, sub_products=["open"])
    auth = _authenticate(cfg, route, {"Ocp-Apim-Subscription-Key": "good"})
    assert auth.subscription is not None and auth.subscription.id == "s1"


def test_mixed_open_and_closed_products_serve_keyless_call_in_open_context() -> None:
    """With an open product, a keyless call is handled in the open product's context.

    https://learn.microsoft.com/en-us/azure/api-management/api-management-subscriptions
    """
    products = {"paid": ProductConfig(name="paid"), "open": ProductConfig(name="open", require_subscription=False)}
    cfg, route = _sub_cfg(anonymous=True, products=products, sub_products=["paid"])
    auth = _authenticate(cfg, route)
    assert enforce_product_grant(cfg, route, auth, subscription_is_bypassed=False) == "open"


def test_closed_product_still_rejects_invalid_key() -> None:
    """Without an open product an invalid key is still a 401.

    https://learn.microsoft.com/en-us/azure/api-management/api-management-subscriptions
    """
    cfg, route = _sub_cfg(anonymous=True, products={"paid": ProductConfig(name="paid")}, sub_products=["paid"])
    with pytest.raises(HTTPException) as exc:
        _authenticate(cfg, route, {"Ocp-Apim-Subscription-Key": "nope"})
    assert exc.value.status_code == 401


def test_empty_key_header_does_not_fall_through_to_query() -> None:
    """The query parameter is checked only if the header isn't present.

    https://learn.microsoft.com/en-us/azure/api-management/api-management-subscriptions
    The docs don't say which 401 an empty value gets; the simulator treats it as a missing key.
    """
    cfg, route = _sub_cfg(anonymous=False, products={"paid": ProductConfig(name="paid")}, sub_products=["paid"])
    with pytest.raises(HTTPException) as exc:
        _authenticate(
            cfg, route, {"Ocp-Apim-Subscription-Key": "", "Authorization": "Bearer abc"}, "subscription-key=good"
        )
    assert exc.value.status_code == 401
    assert "missing subscription key" in exc.value.detail


def test_open_product_still_denies_a_valid_key_scoped_elsewhere() -> None:
    """A real key for a product outside the API is denied even with an open product.

    The summary table's third row (an open product exists, the API requires a
    subscription) denies an "other key not scoped to applicable product or
    API"; only keys that aren't valid at all are ignored.
    https://learn.microsoft.com/en-us/azure/api-management/api-management-subscriptions
    """
    products = {"open": ProductConfig(name="open", require_subscription=False), "other": ProductConfig(name="other")}
    cfg, route = _sub_cfg(anonymous=True, products=products, sub_products=["other"])
    route = route.model_copy(update={"products": ["open"]})
    auth = _authenticate(cfg, route, {"Ocp-Apim-Subscription-Key": "good"})
    with pytest.raises(HTTPException) as exc:
        enforce_product_grant(cfg, route, auth, subscription_is_bypassed=False)
    assert exc.value.status_code == 401
