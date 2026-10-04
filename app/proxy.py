from __future__ import annotations

import re
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Any

from fastapi import Request

from app.config import ApiConfig, ApiVersioningScheme, GatewayConfig, RouteConfig, RouteMatch
from app.security import AuthContext, build_client_principal

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
}

INTERNAL_UPSTREAM_HEADERS = {
    "x-apim-user-object-id",
    "x-apim-user-email",
    "x-apim-user-name",
    "x-apim-auth-method",
    "x-apim-products",
    "x-apim-backend-id",
    "x-apim-managed-identity",
    "x-apim-managed-identity-resource",
    "x-apim-client-certificate",
    "x-apim-client-certificate-thumbprints",
    "x-user-id",
    "x-user-name",
    "x-ms-client-principal",
    "x-ms-client-principal-name",
}


@dataclass(frozen=True)
class ResolvedRoute:
    route: RouteConfig
    upstream_path: str
    api_version: str | None = None
    matched_parameters: dict[str, str] = field(default_factory=dict)
    matched_query_parameters: frozenset[str] = frozenset()


def _normalize_host(host: str) -> str:
    normalized = host.strip().lower()
    if not normalized:
        return ""
    # Prefer the first proxy hop host if a list is forwarded.
    return normalized.split(",", 1)[0].strip()


def _strip_port(host: str) -> str:
    if not host:
        return ""
    # Bracketed IPv6, optionally with port.
    if host.startswith("["):
        end = host.find("]")
        if end != -1:
            return host[: end + 1]
        return host
    if ":" in host:
        left, right = host.rsplit(":", 1)
        if right.isdigit():
            return left
    return host


def _expand_host_candidates(raw_host: str) -> list[str]:
    normalized = _normalize_host(raw_host)
    if not normalized:
        return []

    candidates: list[str] = []
    for value in (normalized, _strip_port(normalized)):
        if value and value not in candidates:
            candidates.append(value)
    return candidates


def _request_host_candidate_groups(request: Request) -> list[list[str]]:
    groups: list[list[str]] = []
    raw_values = [
        request.headers.get("x-forwarded-host", ""),
        request.headers.get("host", ""),
        request.url.hostname or "",
    ]
    for raw in raw_values:
        candidates = _expand_host_candidates(raw)
        if not candidates:
            continue
        if candidates in groups:
            continue
        groups.append(candidates)
    return groups


def _host_pattern_matches(pattern: str, request_host: str) -> bool:
    """Does one host_match entry match one request host?

    Both sides are compared with and without their port, because a route may be
    written as `api.example.com` while the Host header carries `api.example.com:8443`,
    or the other way round. A pattern containing `*` is matched as a glob.
    """
    request_no_port = _strip_port(request_host)
    for candidate in (pattern, _strip_port(pattern)):
        if candidate in (request_host, request_no_port):
            return True
        if "*" in candidate and (fnmatch(request_host, candidate) or fnmatch(request_no_port, candidate)):
            return True
    return False


def _route_matches_host(route: RouteConfig, request_hosts: list[str]) -> bool:
    """A route with no host_match answers for any host; one with it needs a hit."""
    if not route.host_match:
        return True
    if not request_hosts:
        return False
    patterns = [normalized for expected in route.host_match if (normalized := _normalize_host(expected))]
    return any(_host_pattern_matches(pattern, request_host) for pattern in patterns for request_host in request_hosts)


def _available_versions(config: GatewayConfig, *, path: str, version_set: str) -> set[str]:
    versions: set[str] = set()
    for route in config.routes:
        if route.api_version_set != version_set:
            continue
        if not route.matches_api_path(path):
            continue
        if route.api_version:
            versions.add(route.api_version)
    return versions


def _version_matches(candidate: RouteConfig, requested_version: str, scheme: ApiVersioningScheme) -> bool:
    if scheme == ApiVersioningScheme.Segment:
        return bool(candidate.api_version and candidate.api_version.casefold() == requested_version.casefold())
    return candidate.api_version == requested_version


def _request_scheme(request: Request) -> str:
    """Read the externally visible scheme, including the first proxy hop."""
    forwarded = request.headers.get("x-forwarded-proto", "")
    return (forwarded.split(",", 1)[0].strip() or request.url.scheme).casefold()


def _route_protocol_allowed(route: RouteConfig, request: Request) -> bool:
    """Check an API's HTTP protocol restriction.

    The API resource reference documents the protocols field but not the
    gateway's rejected-request status or message. The gateway therefore uses
    its existing 404 Resource not found envelope for a route that is not
    exposed on the request scheme.
    https://learn.microsoft.com/en-us/rest/api/apimanagement/apis/create-or-update
    """
    if route.api_protocols is None:
        return True
    return _request_scheme(request) in {protocol.casefold() for protocol in route.api_protocols}


def _read_version(request: Request, *, config: GatewayConfig, route: RouteConfig, path: str) -> tuple[str | None, str]:
    # Returns (version, upstream_path).
    version_set_id = route.api_version_set
    if not version_set_id:
        return None, path

    version_set = config.api_version_sets.get(version_set_id)
    if version_set is None:
        return None, path

    if version_set.versioning_scheme == ApiVersioningScheme.Header:
        header_name = version_set.version_header_name or ""
        version = request.headers.get(header_name)
        return version or None, path

    if version_set.versioning_scheme == ApiVersioningScheme.Query:
        query_name = version_set.version_query_name or ""
        version = request.query_params.get(query_name)
        return version or None, path

    # Segment scheme: treat the first segment after the API path as the version.
    prefix = (route.api_path_prefix or route.path_prefix).rstrip("/")
    remainder = path
    lowered_path = path.casefold()
    lowered_prefix = prefix.casefold()
    if prefix and (lowered_path == lowered_prefix or lowered_path.startswith(lowered_prefix + "/")):
        remainder = path[len(prefix) :]
    remainder = remainder.lstrip("/")
    first = remainder.split("/", 1)[0] if remainder else ""

    candidates = _available_versions(config, path=path, version_set=version_set_id)
    matching_version = next((candidate for candidate in candidates if candidate.casefold() == first.casefold()), None)
    if matching_version:
        # Strip the version segment for upstream routing to keep the internal API path stable.
        rest = remainder.split("/", 1)[1] if "/" in remainder else ""
        stripped = (prefix + "/" + rest) if rest else prefix
        stripped = stripped or "/"
        return matching_version, stripped
    return None, path


def _match_versioned_candidate(
    candidate: RouteConfig,
    *,
    config: GatewayConfig,
    version_set_id: str,
    scheme: ApiVersioningScheme,
    requested_version: str | None,
    path: str,
    upstream_path: str,
    request: Request,
    request_hosts: list[str],
) -> RouteMatch | None:
    if candidate.api_version_set != version_set_id:
        return None
    if requested_version is None and candidate.api_version is not None:
        return None
    if requested_version is not None and not _version_matches(candidate, requested_version, scheme):
        return None
    if not candidate.matches_api_path(path) or not _route_matches_host(candidate, request_hosts):
        return None
    if not _route_protocol_allowed(candidate, request):
        return None
    api = config.apis.get(candidate.api_id or "")
    if api is not None and api.is_online is False:
        return None
    return candidate.match(method=request.method, path=upstream_path, query=request.query_params)


def _resolve_versioned_route(
    config: GatewayConfig,
    request: Request,
    *,
    route: RouteConfig,
    path: str,
    request_hosts: list[str],
) -> ResolvedRoute | None:
    """Pick the route for the API version this request asked for.

    A version set that does not exist, a request naming an unknown version, and
    a request without a version when no Original API exists are all "no route"
    rather than a fall-through to another version.
    """
    version_set_id = route.api_version_set
    version_set = config.api_version_sets.get(version_set_id)
    if version_set is None:
        return None

    requested_version, upstream_path = _read_version(request, config=config, route=route, path=path)
    best: tuple[RouteMatch, RouteConfig] | None = None
    for candidate in config.routes:
        match = _match_versioned_candidate(
            candidate,
            config=config,
            version_set_id=version_set_id,
            scheme=version_set.versioning_scheme,
            requested_version=requested_version,
            path=path,
            upstream_path=upstream_path,
            request=request,
            request_hosts=request_hosts,
        )
        if match is None:
            continue
        if best is None or match.precedence > best[0].precedence:
            best = (match, candidate)

    if best is None:
        return None
    match, candidate = best
    return ResolvedRoute(
        route=candidate,
        upstream_path=upstream_path,
        api_version=requested_version,
        matched_parameters=match.parameters,
        matched_query_parameters=match.query_parameters,
    )


def _resolve_versioned_candidate(
    config: GatewayConfig,
    request: Request,
    *,
    route: RouteConfig,
    path: str,
    request_hosts: list[str],
) -> tuple[RouteMatch, ResolvedRoute] | None:
    resolved = _resolve_versioned_route(config, request, route=route, path=path, request_hosts=request_hosts)
    if resolved is None:
        return None
    match = resolved.route.match(method=request.method, path=resolved.upstream_path, query=request.query_params)
    return (match, resolved) if match is not None else None


def _resolve_route_candidate(
    config: GatewayConfig,
    request: Request,
    *,
    route: RouteConfig,
    path: str,
    request_hosts: list[str],
    version_cache: dict[tuple[str, str], tuple[RouteMatch, ResolvedRoute] | None],
) -> tuple[RouteMatch, ResolvedRoute] | None:
    if not _route_matches_host(route, request_hosts) or not _route_protocol_allowed(route, request):
        return None
    api_config = config.apis.get(route.api_id or "")
    if api_config is not None and api_config.is_online is False:
        return None
    if not route.api_version_set:
        match = route.match(method=request.method, path=path, query=request.query_params)
        if match is None:
            return None
        return match, ResolvedRoute(
            route=route,
            upstream_path=path,
            matched_parameters=match.parameters,
            matched_query_parameters=match.query_parameters,
        )
    if not route.matches_api_path(path):
        return None
    # Only the version set and effective API prefix vary between source routes
    # in a single request/host group. Retain the exact prefix spelling because
    # segment versioning uses it to construct the upstream path.
    version_key = (route.api_version_set, route.api_path_prefix or route.path_prefix)
    if version_key not in version_cache:
        version_cache[version_key] = _resolve_versioned_candidate(
            config,
            request,
            route=route,
            path=path,
            request_hosts=request_hosts,
        )
    return version_cache[version_key]


def _api_matches_revision_version(
    config: GatewayConfig, api_id: str, api: ApiConfig, request: Request, path: str
) -> bool:
    """Whether this same-path API is selected by the request's version selector."""
    candidate_config = config.model_copy(update={"apis": {api_id: api}, "routes": []})
    candidate_config.routes = candidate_config.materialize_routes()
    for route in candidate_config.routes:
        version_set_id = route.api_version_set
        version_set = candidate_config.api_version_sets.get(version_set_id or "")
        if version_set is None:
            continue
        requested_version, _ = _read_version(request, config=candidate_config, route=route, path=path)
        if requested_version is None:
            if route.api_version is None:
                return True
        elif _version_matches(route, requested_version, version_set.versioning_scheme):
            return True
    return False


def _select_api_revision(config: GatewayConfig, request: Request) -> tuple[GatewayConfig, str] | None:
    # APIM places the revision selector on the API path itself, before the
    # operation path. Resolve against that revision's saved API snapshot, then
    # match the normalized public path so the selector never reaches upstream.
    path = request.scope["path"]
    revision_match = re.match(r"^(.*?);rev=([^/]+)(/.*|$)", path, flags=re.IGNORECASE)
    if revision_match is None:
        return config, path
    api_path, revision_id, suffix = revision_match.groups()
    api_entries = [
        (api_id, candidate)
        for api_id, candidate in config.apis.items()
        if ("/" + candidate.path.strip("/")).rstrip("/").casefold() == (api_path.rstrip("/") or "/").casefold()
    ]
    normalized_path = f"{api_path}{suffix}"
    if len(api_entries) > 1:
        api_entries = [
            entry for entry in api_entries if _api_matches_revision_version(config, *entry, request, normalized_path)
        ]
    if len(api_entries) != 1:
        return None
    api_id, api = api_entries[0]
    revision = api.revisions.get(revision_id)
    if revision is None or revision.is_online is False or not revision.definition:
        return None
    revision_api = ApiConfig.model_validate(
        {**revision.definition, "revisions": api.revisions, "releases": api.releases}
    )
    config = config.model_copy(update={"apis": {api_id: revision_api}, "routes": []})
    config.routes = config.materialize_routes()
    request.scope["path"] = normalized_path
    request.scope["raw_path"] = normalized_path.encode()
    return config, normalized_path


def resolve_route(config: GatewayConfig, request: Request) -> ResolvedRoute | None:
    selected = _select_api_revision(config, request)
    if selected is None:
        return None
    config, path = selected
    request_host_groups = _request_host_candidate_groups(request)
    if not request_host_groups:
        request_host_groups = [[]]

    for request_hosts in request_host_groups:
        # Both successful and missing selections are request-local. Host
        # fallback must rescan with its own host group, never a cached result
        # from a different forwarded or direct host.
        version_cache: dict[tuple[str, str], tuple[RouteMatch, ResolvedRoute] | None] = {}
        candidates: list[tuple[RouteMatch, int, ResolvedRoute]] = []
        for index, route in enumerate(config.routes):
            candidate = _resolve_route_candidate(
                config,
                request,
                route=route,
                path=path,
                request_hosts=request_hosts,
                version_cache=version_cache,
            )
            if candidate is None:
                continue
            match, resolved = candidate
            candidates.append((match, index, resolved))

        if candidates:
            _, _, best_route = max(candidates, key=lambda item: (item[0].precedence, -item[1]))
            return best_route

    return None


def apply_claim_headers(headers: dict[str, str], claims: dict[str, Any]) -> None:
    headers["x-apim-user-object-id"] = str(claims.get("sub", ""))
    headers["x-apim-user-email"] = str(claims.get("email", ""))
    headers["x-apim-user-name"] = str(claims.get("name") or claims.get("preferred_username") or "")
    headers["x-apim-auth-method"] = "oidc"
    headers["x-ms-client-principal"] = build_client_principal(claims)
    headers["x-ms-client-principal-name"] = str(claims.get("preferred_username", ""))


def build_upstream_headers(
    request: Request,
    auth: AuthContext,
    *,
    inject_simulator_identity_headers: bool = False,
) -> dict[str, str]:
    headers: dict[str, str] = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in HOP_BY_HOP_HEADERS and key.lower() not in INTERNAL_UPSTREAM_HEADERS
    }

    client_host = request.client.host if request.client else ""
    incoming_forwarded_for = request.headers.get("x-forwarded-for", "").strip()
    if client_host:
        # Microsoft documents that APIM adds X-Forwarded-For and that a policy
        # cannot remove the client IP. The docs do not specify exact list
        # formatting, so use the standard comma-separated proxy form.
        headers["x-forwarded-for"] = (
            f"{incoming_forwarded_for}, {client_host}" if incoming_forwarded_for else client_host
        )

    if inject_simulator_identity_headers:
        apply_claim_headers(headers, auth.claims)

    if inject_simulator_identity_headers and auth.subscription is not None:
        headers["x-user-id"] = auth.subscription.id
        headers["x-user-name"] = auth.subscription.name
        if auth.subscription_products:
            headers["x-apim-products"] = ",".join(auth.subscription_products)

    return headers


def filter_response_headers(upstream_headers: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in upstream_headers.items() if key.lower() not in HOP_BY_HOP_HEADERS}


def build_user_payload(auth: AuthContext, issuer: str | None, audience: str | None) -> dict[str, Any]:
    claims = auth.claims
    return {
        "name": claims.get("name") or claims.get("preferred_username"),
        "email": claims.get("email"),
        "preferred_username": claims.get("preferred_username"),
        "sub": claims.get("sub"),
        "issuer": issuer or claims.get("iss"),
        "aud": audience or claims.get("aud"),
        "subscription": auth.subscription.model_dump() if auth.subscription is not None else None,
        "products": auth.subscription_products,
    }
