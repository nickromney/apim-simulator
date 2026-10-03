"""Composition tests: pin the public route inventory.

Written before decomposing app/main.py into routers: any decomposition must
preserve exactly this method/path surface. If you add or remove an endpoint
deliberately, update the inventory below in the same change.
"""

from app.main import create_app

EXPECTED_ROUTES = {
    ("GET", "/apim/portal/apis/{api_id}/openapi"),
    ("GET", "/apim/portal/reference"),
    ("GET", "/apim/portal/assets/{asset}"),
    ("GET", "/apim/management/security/events"),
    ("GET", "/apim/management/security/governance"),
    ("GET", "/apim/management/security/posture"),
    ("GET", "/apim/management/security/threats"),
    ("POST", "/apim/security/backups"),
    ("POST", "/apim/security/restore"),
    ("PUT", "/apim/management/security/governance"),
    ("DELETE", "/apim/management/apis/{api_id}/resolvers/{resolver_id}"),
    ("DELETE", "/apim/management/loggers/{logger_id}"),
    ("GET", "/apim/management/apis/{api_id}/graphql"),
    ("GET", "/apim/management/apis/{api_id}/resolvers"),
    ("GET", "/crossdomain.xml"),
    ("PUT", "/apim/management/apis/{api_id}/graphql"),
    ("PUT", "/apim/management/apis/{api_id}/resolvers/{resolver_id}"),
    ("PUT", "/apim/management/loggers/{logger_id}"),
    ("DELETE", "/apim/management/api-center/link"),
    ("DELETE", "/apim/management/monitoring/alert-rules/{rule_id}"),
    ("GET", "/apim/management/api-center/link"),
    ("GET", "/apim/management/api-centers"),
    ("GET", "/apim/management/api-centers/{center_id}/apis"),
    ("GET", "/apim/management/apis/{api_id}/export"),
    ("GET", "/apim/management/monitoring/activity-logs"),
    ("GET", "/apim/management/monitoring/alerts"),
    ("GET", "/apim/management/monitoring/diagnostic-settings"),
    ("GET", "/apim/management/monitoring/metrics"),
    ("GET", "/apim/management/monitoring/resource-logs"),
    ("GET", "/apim/portal/content"),
    ("GET", "/apim/portal/editor"),
    ("GET", "/apim/portal/editor/draft"),
    ("GET", "/apim/portal/editor/preview"),
    ("GET", "/apim/portal/media/{media_id}"),
    ("GET", "/apim/portal/pages/{slug}"),
    ("POST", "/apim/management/apis/{api_id}/versions"),
    ("POST", "/apim/management/gateways/{gateway_id}/listDebugCredentials"),
    ("POST", "/apim/management/gateways/{gateway_id}/listTrace"),
    ("POST", "/apim/portal/editor/media"),
    ("POST", "/apim/portal/editor/publish"),
    ("PUT", "/apim/management/api-center/link"),
    ("PUT", "/apim/management/api-centers/{center_id}"),
    ("PUT", "/apim/management/monitoring/alert-rules/{rule_id}"),
    ("PUT", "/apim/management/monitoring/diagnostic-settings"),
    ("PUT", "/apim/portal/editor/draft"),
    ("DELETE", "/apim/management/api-version-sets/{version_set_id}"),
    ("DELETE", "/apim/management/apis/{api_id}"),
    ("DELETE", "/apim/management/apis/{api_id}/operations/{operation_id}"),
    ("DELETE", "/apim/management/apis/{api_id}/operations/{operation_id}/tags/{tag_id}"),
    ("DELETE", "/apim/management/apis/{api_id}/releases/{release_id}"),
    ("DELETE", "/apim/management/apis/{api_id}/revisions/{revision_id}"),
    ("DELETE", "/apim/management/apis/{api_id}/tags/{tag_id}"),
    ("DELETE", "/apim/management/backends/{backend_id}"),
    ("DELETE", "/apim/management/groups/{group_id}"),
    ("DELETE", "/apim/management/groups/{group_id}/users/{user_id}"),
    ("DELETE", "/apim/management/named-values/{named_value_id}"),
    ("DELETE", "/apim/management/policy-fragments/{fragment_id}"),
    ("DELETE", "/apim/management/products/{product_id}"),
    ("DELETE", "/apim/management/products/{product_id}/groups/{group_id}"),
    ("DELETE", "/apim/management/products/{product_id}/tags/{tag_id}"),
    ("DELETE", "/apim/management/subscriptions/{subscription_id}"),
    ("DELETE", "/apim/management/tags/{tag_id}"),
    ("DELETE", "/apim/management/users/{user_id}"),
    ("DELETE", "/{full_path:path}"),
    ("GET", "/"),
    ("GET", "/apim/health"),
    ("GET", "/apim/management/api-version-sets"),
    ("GET", "/apim/management/api-version-sets/{version_set_id}"),
    ("GET", "/apim/management/apis"),
    ("GET", "/apim/management/apis/{api_id}"),
    ("GET", "/apim/management/apis/{api_id}/operations"),
    ("GET", "/apim/management/apis/{api_id}/operations/{operation_id}"),
    ("GET", "/apim/management/apis/{api_id}/operations/{operation_id}/tags"),
    ("GET", "/apim/management/apis/{api_id}/operations/{operation_id}/tags/{tag_id}"),
    ("GET", "/apim/management/apis/{api_id}/releases"),
    ("GET", "/apim/management/apis/{api_id}/releases/{release_id}"),
    ("GET", "/apim/management/apis/{api_id}/revisions"),
    ("GET", "/apim/management/apis/{api_id}/revisions/{revision_id}"),
    ("GET", "/apim/management/apis/{api_id}/schemas"),
    ("GET", "/apim/management/apis/{api_id}/schemas/{schema_id}"),
    ("GET", "/apim/management/apis/{api_id}/tags"),
    ("GET", "/apim/management/apis/{api_id}/tags/{tag_id}"),
    ("GET", "/apim/management/backends"),
    ("GET", "/apim/management/backends/{backend_id}"),
    ("GET", "/apim/management/diagnostics"),
    ("GET", "/apim/management/diagnostics/{diagnostic_id}"),
    ("GET", "/apim/management/groups"),
    ("GET", "/apim/management/groups/{group_id}"),
    ("GET", "/apim/management/groups/{group_id}/users"),
    ("GET", "/apim/management/groups/{group_id}/users/{user_id}"),
    ("GET", "/apim/management/loggers"),
    ("GET", "/apim/management/loggers/{logger_id}"),
    ("GET", "/apim/management/named-values"),
    ("GET", "/apim/management/named-values/{named_value_id}"),
    ("GET", "/apim/management/operations"),
    ("GET", "/apim/management/policies/{scope_type}/{scope_name:path}"),
    ("GET", "/apim/management/policy-fragments"),
    ("GET", "/apim/management/policy-fragments/{fragment_id}"),
    ("GET", "/apim/management/products"),
    ("GET", "/apim/management/products/{product_id}"),
    ("GET", "/apim/management/products/{product_id}/groups"),
    ("GET", "/apim/management/products/{product_id}/groups/{group_id}"),
    ("GET", "/apim/management/products/{product_id}/tags"),
    ("GET", "/apim/management/products/{product_id}/tags/{tag_id}"),
    ("GET", "/apim/management/service"),
    ("GET", "/apim/management/status"),
    ("GET", "/apim/management/subscriptions"),
    ("GET", "/apim/management/subscriptions/{subscription_id}"),
    ("GET", "/apim/management/summary"),
    ("GET", "/apim/management/tags"),
    ("GET", "/apim/management/tags/{tag_id}"),
    ("GET", "/apim/management/traces"),
    ("GET", "/apim/management/users"),
    ("GET", "/apim/management/users/{user_id}"),
    ("GET", "/apim/portal"),
    ("GET", "/apim/portal/catalog"),
    ("GET", "/apim/portal/subscriptions"),
    ("GET", "/apim/portal/users"),
    ("GET", "/apim/startup"),
    ("GET", "/apim/trace/{trace_id}"),
    ("GET", "/apim/user"),
    ("GET", "/docs"),
    ("GET", "/docs/oauth2-redirect"),
    ("GET", "/openapi.json"),
    ("GET", "/redoc"),
    ("GET", "/{full_path:path}"),
    ("PATCH", "/apim/management/subscriptions/{subscription_id}"),
    ("PATCH", "/{full_path:path}"),
    ("POST", "/apim/admin/subscriptions/{subscription_id}/rotate"),
    ("POST", "/apim/management/apis/{api_id}/import"),
    ("POST", "/apim/management/import/tofu-show"),
    ("POST", "/apim/management/replay"),
    ("POST", "/apim/management/subscriptions"),
    ("POST", "/apim/management/subscriptions/{subscription_id}/rotate"),
    ("POST", "/apim/portal/subscriptions"),
    ("POST", "/apim/reload"),
    ("POST", "/{full_path:path}"),
    ("PUT", "/apim/management/api-version-sets/{version_set_id}"),
    ("PUT", "/apim/management/apis/{api_id}"),
    ("PUT", "/apim/management/apis/{api_id}/operations/{operation_id}"),
    ("PUT", "/apim/management/apis/{api_id}/operations/{operation_id}/tags/{tag_id}"),
    ("PUT", "/apim/management/apis/{api_id}/releases/{release_id}"),
    ("PUT", "/apim/management/apis/{api_id}/revisions/{revision_id}"),
    ("PUT", "/apim/management/apis/{api_id}/tags/{tag_id}"),
    ("PUT", "/apim/management/backends/{backend_id}"),
    ("PUT", "/apim/management/groups/{group_id}"),
    ("PUT", "/apim/management/groups/{group_id}/users/{user_id}"),
    ("PUT", "/apim/management/named-values/{named_value_id}"),
    ("PUT", "/apim/management/policies/{scope_type}/{scope_name:path}"),
    ("PUT", "/apim/management/policy-fragments/{fragment_id}"),
    ("PUT", "/apim/management/products/{product_id}"),
    ("PUT", "/apim/management/products/{product_id}/groups/{group_id}"),
    ("PUT", "/apim/management/products/{product_id}/tags/{tag_id}"),
    ("PUT", "/apim/management/tags/{tag_id}"),
    ("PUT", "/apim/management/users/{user_id}"),
    ("PUT", "/{full_path:path}"),
}


def _flatten_routes(routes):
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from _flatten_routes(included.routes)
        else:
            yield route


def test_route_inventory_is_stable() -> None:
    app = create_app()
    actual = {
        (method, route.path)
        for route in _flatten_routes(app.routes)
        if hasattr(route, "methods")
        for method in (route.methods or set())
        if method not in {"HEAD", "OPTIONS"}
    }
    missing = EXPECTED_ROUTES - actual
    extra = actual - EXPECTED_ROUTES
    assert not missing, f"routes lost by refactor: {sorted(missing)}"
    assert not extra, f"routes added unexpectedly: {sorted(extra)}"


def test_gateway_catch_all_is_registered_last() -> None:
    """The catch-all must stay behind every management and portal route.

    Starlette matches routes in registration order, so a catch-all that drifts
    ahead of the management surface swallows it whole while every route above
    still reports as present. The inventory test cannot see that; this can.
    """
    app = create_app()
    paths = [route.path for route in _flatten_routes(app.routes) if hasattr(route, "methods")]
    catch_all_positions = [index for index, path in enumerate(paths) if path == "/{full_path:path}"]

    assert catch_all_positions, "gateway catch-all route is missing"
    specific_positions = [index for index, path in enumerate(paths) if path.startswith("/apim/")]
    assert specific_positions, "no /apim/ routes registered"
    assert max(specific_positions) < min(catch_all_positions), (
        "the gateway catch-all is registered ahead of /apim/ routes, which shadows them"
    )
