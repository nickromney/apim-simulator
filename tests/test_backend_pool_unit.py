"""Direct unit tests for backend pool selection, health and credentials.

Mutation testing put a number on why these are needed. `app/backend_pool.py`
reads as 88% covered, but 106 of its mutants had no test that reached them at
all: the module was exercised only incidentally, through gateway requests that
happened to pass through it. A test that reaches a line by accident cannot be
relied on to notice when that line changes.

These drive the module directly, and lean on the decisions the round-robin and
the circuit breaker actually make: boundaries, rotation order, and priority.
"""

from __future__ import annotations

import pytest

from app.backend_pool import (
    apply_backend_credentials,
    backend_health_entry,
    pool_member_breaker,
    record_backend_result,
    select_pool_member,
)
from app.config import (
    BackendCircuitBreakerConfig,
    BackendCodeRange,
    BackendConfig,
    BackendPoolMemberConfig,
    BackendSessionAffinityConfig,
    BackendSessionIdConfig,
    GatewayConfig,
    NamedValueConfig,
)
from app.policy import PolicyRequest, issue_local_managed_identity_token


def _member(backend_id: str, *, weight: int = 1, priority: int = 1) -> BackendPoolMemberConfig:
    return BackendPoolMemberConfig(backend_id=backend_id, weight=weight, priority=priority)


def _pool(*members: BackendPoolMemberConfig) -> BackendConfig:
    return BackendConfig(url="https://pool.invalid", type="pool", pool=list(members))


def _empty_pool() -> BackendConfig:
    """A pool with no members at all.

    GatewayConfig refuses to build one, so this bypasses validation deliberately:
    select_pool_member still guards the case, and an unguarded guard is worth
    knowing about even when today's config cannot reach it.
    """
    return BackendConfig.model_construct(url="https://pool.invalid", type="pool", pool=[])


def _config(*backend_ids: str, named_values: dict[str, str] | None = None) -> GatewayConfig:
    return GatewayConfig(
        allow_anonymous=True,
        workload_identity={"mode": "demo"},
        backends={bid: BackendConfig(url=f"https://{bid}.invalid") for bid in backend_ids},
        named_values={name: NamedValueConfig(value=value) for name, value in (named_values or {}).items()},
    )


# --- health bookkeeping ----------------------------------------------------


def test_health_entry_is_created_with_a_clean_slate() -> None:
    health: dict = {}
    entry = backend_health_entry(health, "a")

    assert entry == {"failures": [], "open_until": 0.0}
    assert health["a"] is entry


def test_health_entry_is_replaced_when_the_stored_value_is_not_a_dict() -> None:
    """The store is plain app state, so a wrong shape must not crash selection."""
    health: dict = {"a": "corrupted"}

    assert backend_health_entry(health, "a") == {"failures": [], "open_until": 0.0}


def test_a_success_clears_accumulated_failures() -> None:
    breaker = BackendCircuitBreakerConfig(failure_count=3)
    health: dict = {}
    record_backend_result(health, breaker, "a", now=100.0, failed=True)
    record_backend_result(health, breaker, "a", now=101.0, failed=False)

    assert health["a"]["failures"] == []


def test_the_breaker_stays_closed_below_the_failure_count() -> None:
    breaker = BackendCircuitBreakerConfig(failure_count=3, interval_seconds=60.0)
    health: dict = {}
    record_backend_result(health, breaker, "a", now=100.0, failed=True)
    record_backend_result(health, breaker, "a", now=101.0, failed=True)

    assert health["a"]["open_until"] == 0.0
    assert len(health["a"]["failures"]) == 2


def test_the_breaker_trips_exactly_on_the_failure_count() -> None:
    """The boundary itself: two failures is closed, the third opens it."""
    breaker = BackendCircuitBreakerConfig(failure_count=3, interval_seconds=60.0, trip_duration_seconds=30.0)
    health: dict = {}
    for offset in range(3):
        record_backend_result(health, breaker, "a", now=100.0 + offset, failed=True)

    assert health["a"]["open_until"] == pytest.approx(132.0)
    assert health["a"]["failures"] == []


def test_failures_older_than_the_interval_do_not_count_towards_a_trip() -> None:
    breaker = BackendCircuitBreakerConfig(failure_count=2, interval_seconds=10.0)
    health: dict = {}
    record_backend_result(health, breaker, "a", now=100.0, failed=True)
    record_backend_result(health, breaker, "a", now=200.0, failed=True)

    assert health["a"]["open_until"] == 0.0, "a failure 100s outside the window should have aged out"


def test_a_failure_count_below_one_still_trips_on_the_first_failure() -> None:
    breaker = BackendCircuitBreakerConfig(failure_count=0, trip_duration_seconds=5.0)
    health: dict = {}
    record_backend_result(health, breaker, "a", now=10.0, failed=True)

    assert health["a"]["open_until"] == pytest.approx(15.0)


# --- which breaker applies -------------------------------------------------


def test_a_member_breaker_overrides_the_pool_breaker() -> None:
    member_breaker = BackendCircuitBreakerConfig(failure_count=1)
    pool_breaker = BackendCircuitBreakerConfig(failure_count=9)
    member = BackendConfig(url="https://a.invalid", circuit_breaker=member_breaker)
    pool = BackendConfig(url="https://pool.invalid", type="pool", pool=[_member("a")], circuit_breaker=pool_breaker)

    assert pool_member_breaker(pool, member) is member_breaker


def test_the_pool_breaker_applies_when_the_member_declares_none() -> None:
    pool_breaker = BackendCircuitBreakerConfig(failure_count=9)
    member = BackendConfig(url="https://a.invalid")
    pool = BackendConfig(url="https://pool.invalid", type="pool", pool=[_member("a")], circuit_breaker=pool_breaker)

    assert pool_member_breaker(pool, member) is pool_breaker


def test_a_default_breaker_applies_when_neither_declares_one() -> None:
    member = BackendConfig(url="https://a.invalid")
    pool = BackendConfig(url="https://pool.invalid", type="pool", pool=[_member("a")])

    assert pool_member_breaker(pool, member).failure_count == 3


def test_circuit_breaker_supports_documented_failure_condition_shape() -> None:
    """APIM circuit breakers use status ranges, error reasons, and retry-after.

    https://learn.microsoft.com/en-us/azure/api-management/backends
    """
    breaker = BackendCircuitBreakerConfig(
        failure_count=2,
        interval_seconds=60,
        trip_duration_seconds=30,
        status_code_ranges=[BackendCodeRange(min=500, max=599)],
        error_reasons=["Server errors"],
        accept_retry_after=True,
    )

    assert breaker.status_code_ranges[0].min == 500
    assert breaker.error_reasons == ["Server errors"]
    assert breaker.accept_retry_after is True


def test_session_affinity_uses_a_cookie_session_id() -> None:
    """APIM pool session awareness is configured with a cookie session ID.

    https://learn.microsoft.com/en-us/azure/api-management/backends
    """
    pool = BackendConfig(
        type="pool",
        pool=[_member("a")],
        session_affinity=BackendSessionAffinityConfig(
            session_id=BackendSessionIdConfig(source="Cookie", name="SessionId")
        ),
    )

    assert pool.session_affinity.session_id.name == "SessionId"


# --- selection -------------------------------------------------------------


def test_a_pool_with_no_members_selects_nothing() -> None:
    assert select_pool_member(_config(), {}, "p", _empty_pool(), now=0.0) is None


def test_members_naming_an_unknown_backend_are_ignored() -> None:
    cfg = _config("a")
    pool = _pool(_member("a"), _member("ghost"))

    selected = select_pool_member(cfg, {}, "p", pool, now=0.0)

    assert selected is not None and selected[0] == "a"


def test_a_pool_whose_members_are_all_unknown_selects_nothing() -> None:
    assert select_pool_member(_config("a"), {}, "p", _pool(_member("ghost")), now=0.0) is None


def test_round_robin_rotates_across_calls() -> None:
    cfg = _config("a", "b", "c")
    pool = _pool(_member("a"), _member("b"), _member("c"))
    health: dict = {}

    picked = [select_pool_member(cfg, health, "p", pool, now=0.0)[0] for _ in range(6)]

    assert picked == ["a", "b", "c", "a", "b", "c"], "selection should cycle, not stick or restart"


def test_weight_gives_a_member_proportionally_more_of_the_rotation() -> None:
    cfg = _config("a", "b")
    pool = _pool(_member("a", weight=3), _member("b", weight=1))
    health: dict = {}

    picked = [select_pool_member(cfg, health, "p", pool, now=0.0)[0] for _ in range(8)]

    assert picked.count("a") == 6
    assert picked.count("b") == 2


def test_a_weight_below_one_still_earns_one_slot() -> None:
    cfg = _config("a", "b")
    pool = _pool(_member("a", weight=0), _member("b", weight=0))
    health: dict = {}

    picked = [select_pool_member(cfg, health, "p", pool, now=0.0)[0] for _ in range(4)]

    assert picked == ["a", "b", "a", "b"]


def test_a_lower_priority_number_wins_while_it_is_healthy() -> None:
    cfg = _config("primary", "secondary")
    pool = _pool(_member("primary", priority=1), _member("secondary", priority=2))
    health: dict = {}

    picked = {select_pool_member(cfg, health, "p", pool, now=0.0)[0] for _ in range(4)}

    assert picked == {"primary"}, "a healthy priority-1 member should never yield to priority 2"


def test_selection_falls_to_the_next_priority_when_the_first_is_open() -> None:
    cfg = _config("primary", "secondary")
    pool = _pool(_member("primary", priority=1), _member("secondary", priority=2))
    health = {"primary": {"failures": [], "open_until": 100.0}}

    selected = select_pool_member(cfg, health, "p", pool, now=50.0)

    assert selected is not None and selected[0] == "secondary"


def test_an_open_circuit_closes_again_exactly_at_its_expiry() -> None:
    """`open_until <= now` is the boundary: at the instant it expires, it is usable."""
    cfg = _config("primary", "secondary")
    pool = _pool(_member("primary", priority=1), _member("secondary", priority=2))
    health = {"primary": {"failures": [], "open_until": 100.0}}

    assert select_pool_member(cfg, health, "p", pool, now=99.9)[0] == "secondary"
    assert select_pool_member(cfg, health, "p", pool, now=100.0)[0] == "primary"


def test_a_pool_with_every_member_open_selects_nothing() -> None:
    cfg = _config("a", "b")
    pool = _pool(_member("a"), _member("b"))
    health = {
        "a": {"failures": [], "open_until": 100.0},
        "b": {"failures": [], "open_until": 100.0},
    }

    assert select_pool_member(cfg, health, "p", pool, now=50.0) is None


def test_rotation_state_is_kept_per_pool() -> None:
    """Two pools must not share a cursor, or one advances the other."""
    cfg = _config("a", "b")
    pool = _pool(_member("a"), _member("b"))
    health: dict = {}

    assert select_pool_member(cfg, health, "left", pool, now=0.0)[0] == "a"
    assert select_pool_member(cfg, health, "right", pool, now=0.0)[0] == "a"


# --- backend credentials ---------------------------------------------------
#
# Mutation testing named these three helpers specifically: every mutant in
# _apply_auth_type, _apply_authorization_header and _apply_credential_pairs was
# reported as "no tests", meaning nothing in the suite reached them at all.


def _policy_request(**headers: str) -> PolicyRequest:
    return PolicyRequest(method="GET", path="/x", query={}, headers=dict(headers), variables={})


def _backend(**fields) -> BackendConfig:
    return BackendConfig(url="https://upstream.invalid", allow_simulated_certificate=True, **fields)


def test_basic_auth_returns_credentials_for_the_upstream_call() -> None:
    req = _policy_request()
    auth = apply_backend_credentials(
        _backend(auth_type="basic", basic_username="user", basic_password="pass"), req, _config()
    )

    assert auth == ("user", "pass")


def test_basic_auth_replaces_an_authorization_header_the_caller_already_set() -> None:
    """Backend basic authentication replaces a caller's Authorization header."""
    req = _policy_request(authorization="Bearer caller-token")
    auth = apply_backend_credentials(
        _backend(auth_type="basic", basic_username="user", basic_password="pass"), req, _config()
    )

    assert auth == ("user", "pass")


@pytest.mark.parametrize(
    ("username", "password"),
    [(None, "pass"), ("user", None), (None, None)],
    ids=["no-username", "no-password", "neither"],
)
def test_basic_auth_needs_both_halves(username: str | None, password: str | None) -> None:
    req = _policy_request()
    auth = apply_backend_credentials(
        _backend(auth_type="basic", basic_username=username, basic_password=password), req, _config()
    )

    assert auth is None


def test_managed_identity_uses_a_local_bearer_token() -> None:
    """The simulator uses an opaque local bearer-token adaptation."""
    req = _policy_request()
    auth = apply_backend_credentials(
        _backend(auth_type="managed_identity", managed_identity_resource="https://vault.invalid"), req, _config()
    )

    assert auth is None
    assert req.headers["authorization"].startswith("Bearer local-apim-mi.")
    assert "x-apim-managed-identity" not in req.headers
    assert "x-apim-managed-identity-resource" not in req.headers


def test_managed_identity_without_a_resource_sets_only_the_flag() -> None:
    req = _policy_request()
    apply_backend_credentials(_backend(auth_type="managed_identity"), req, _config())

    assert req.headers["authorization"].startswith("Bearer local-apim-mi.")
    assert "x-apim-managed-identity" not in req.headers


def test_client_certificate_auth_is_signalled_as_a_header() -> None:
    req = _policy_request()
    apply_backend_credentials(_backend(auth_type="client_certificate"), req, _config())

    assert req.headers["x-apim-client-certificate"] == "present"


def test_an_unset_auth_type_adds_nothing() -> None:
    # Accepted equivalents: _apply_auth_type__mutmut_4 and __mutmut_5 rewrite the
    # "none" fallback. Nothing compares against it: an auth type that matches
    # none of the three branches falls through, whatever it is called.
    req = _policy_request()
    auth = apply_backend_credentials(_backend(), req, _config())

    assert auth is None
    assert req.headers == {}


def test_the_auth_type_is_matched_case_insensitively() -> None:
    req = _policy_request()
    auth = apply_backend_credentials(
        _backend(auth_type="BASIC", basic_username="user", basic_password="pass"), req, _config()
    )

    assert auth == ("user", "pass")


def test_an_explicit_authorization_scheme_and_parameter_are_joined() -> None:
    req = _policy_request()
    apply_backend_credentials(
        _backend(authorization_scheme="Bearer", authorization_parameter="token-value"), req, _config()
    )

    assert req.headers["authorization"] == "Bearer token-value"


def test_an_authorization_scheme_without_a_parameter_sets_nothing() -> None:
    req = _policy_request()
    apply_backend_credentials(_backend(authorization_scheme="Bearer"), req, _config())

    assert "authorization" not in req.headers


def test_an_explicit_authorization_never_overwrites_the_caller_s_own() -> None:
    req = _policy_request(authorization="Bearer caller-token")
    apply_backend_credentials(
        _backend(authorization_scheme="Bearer", authorization_parameter="backend-token"), req, _config()
    )

    assert req.headers["authorization"] == "Bearer caller-token"


def test_header_credentials_are_added_lowercased() -> None:
    req = _policy_request()
    apply_backend_credentials(_backend(header_credentials={"X-Api-Key": "secret"}), req, _config())

    assert req.headers["x-api-key"] == "secret"


def test_query_credentials_are_added_to_the_upstream_query() -> None:
    req = _policy_request()
    apply_backend_credentials(_backend(query_credentials={"code": "secret"}), req, _config())

    assert req.query["code"] == "secret"


def test_client_certificate_thumbprints_are_joined_into_one_header() -> None:
    req = _policy_request()
    apply_backend_credentials(_backend(client_certificate_thumbprints=["AA11", "BB22"]), req, _config())

    assert req.headers["x-apim-client-certificate-thumbprints"] == "AA11,BB22"


def test_no_thumbprint_header_is_set_when_none_are_configured() -> None:
    req = _policy_request()
    apply_backend_credentials(_backend(), req, _config())

    assert "x-apim-client-certificate-thumbprints" not in req.headers


# --- health bookkeeping the suite reached only by accident ------------------


def test_a_repaired_health_entry_is_stored_back_on_the_health_map() -> None:
    """The caller mutates the entry it is handed, so it has to be the stored one.

    Accepted equivalents: backend_health_entry__mutmut_3 and __mutmut_5 change
    the setdefault default to None or drop it. The isinstance check below
    replaces anything that is not a dict, so the default never reaches a caller.
    """
    health: dict = {"b": "not an entry"}

    entry = backend_health_entry(health, "b")
    entry["open_until"] = 5.0

    assert health["b"] is entry
    assert backend_health_entry(health, "b")["open_until"] == 5.0


def test_an_entry_without_a_failures_key_records_a_failure() -> None:
    health: dict = {"b": {"open_until": 0.0}}

    record_backend_result(health, BackendCircuitBreakerConfig(failure_count=2), "b", now=10.0, failed=True)

    assert health["b"]["failures"] == [10.0]


def test_a_failure_exactly_one_interval_old_has_expired() -> None:
    breaker = BackendCircuitBreakerConfig(failure_count=2, interval_seconds=10, trip_duration_seconds=30)
    health: dict = {"b": {"failures": [0.0], "open_until": 0.0}}

    record_backend_result(health, breaker, "b", now=10.0, failed=True)

    assert health["b"]["open_until"] == 0.0
    assert health["b"]["failures"] == [10.0]


# --- rotation direction and cursor -----------------------------------------


def test_the_rotation_walks_forward_past_an_open_circuit() -> None:
    cfg = _config("a", "b", "c")
    pool = _pool(_member("a"), _member("b"), _member("c"))
    health: dict = {"a": {"failures": [], "open_until": 100.0}}

    assert select_pool_member(cfg, health, "p", pool, now=0.0)[0] == "b"


def test_the_cursor_resumes_after_the_member_that_was_chosen() -> None:
    cfg = _config("a", "b", "c")
    pool = _pool(_member("a"), _member("b"), _member("c"))
    health: dict = {"a": {"failures": [], "open_until": 100.0}}

    assert select_pool_member(cfg, health, "p", pool, now=0.0)[0] == "b"
    assert select_pool_member(cfg, health, "p", pool, now=0.0)[0] == "c"


def test_an_entry_without_an_open_until_counts_as_closed() -> None:
    cfg = _config("a")
    pool = _pool(_member("a"))
    health: dict = {"a": {"failures": []}}

    assert select_pool_member(cfg, health, "p", pool, now=0.0)[0] == "a"


# --- credentials are policy values, not literals ----------------------------
#
# Every credential goes through render_policy_value, so it can read the request
# and resolve a named value from the gateway config. Nothing asserted that until
# these: a literal renders to itself either way, and mutants that dropped the
# request or the config went unnoticed.


def test_basic_auth_credentials_are_rendered_against_the_request_and_config() -> None:
    req = _policy_request(**{"x-tenant": "acme"})
    auth = apply_backend_credentials(
        _backend(
            auth_type="basic",
            basic_username='@($"{context.Request.Headers.GetValueOrDefault("x-tenant", "")}-{{realm}}")',
            basic_password='@($"{{upstream-secret}}-{context.Request.Headers.GetValueOrDefault("x-tenant", "")}")',
        ),
        req,
        _config(named_values={"upstream-secret": "s3cret", "realm": "eu"}),
    )

    assert auth == ("acme-eu", "s3cret-acme")


def test_the_managed_identity_resource_is_rendered() -> None:
    req = _policy_request(**{"x-tenant": "acme"})
    apply_backend_credentials(
        _backend(
            auth_type="managed_identity",
            managed_identity_resource='@($"{context.Request.Headers.GetValueOrDefault("x-tenant", "")}/{{scope}}")',
        ),
        req,
        _config(named_values={"scope": ".default"}),
    )

    assert req.headers["authorization"] == f"Bearer {issue_local_managed_identity_token('acme/.default')}"


def test_an_explicit_authorization_is_rendered_from_both_halves() -> None:
    req = _policy_request(**{"x-scheme": "Bearer"})
    apply_backend_credentials(
        _backend(
            authorization_scheme='@($"{context.Request.Headers.GetValueOrDefault("x-scheme", "")}{{scheme-suffix}}")',
            authorization_parameter='@($"{{token}}-{context.Request.Headers.GetValueOrDefault("x-scheme", "")}")',
        ),
        req,
        _config(named_values={"token": "abc123", "scheme-suffix": "+"}),
    )

    assert req.headers["authorization"] == "Bearer+ abc123-Bearer"


def test_an_authorization_half_that_renders_empty_contributes_nothing() -> None:
    req = _policy_request()
    apply_backend_credentials(
        _backend(authorization_scheme='@("")', authorization_parameter="opaque-token"),
        req,
        _config(),
    )

    assert req.headers["authorization"] == "opaque-token"


def test_an_authorization_parameter_that_renders_empty_leaves_the_scheme_alone() -> None:
    req = _policy_request()
    apply_backend_credentials(
        _backend(authorization_scheme="Negotiate", authorization_parameter='@("")'),
        req,
        _config(),
    )

    assert req.headers["authorization"] == "Negotiate"


def test_header_and_query_credentials_are_rendered() -> None:
    req = _policy_request(**{"x-tenant": "acme"})
    apply_backend_credentials(
        _backend(
            header_credentials={
                "X-Api-Key": '@($"{{api-key}}-{context.Request.Headers.GetValueOrDefault("x-tenant", "")}")'
            },
            query_credentials={
                "tenant": '@($"{context.Request.Headers.GetValueOrDefault("x-tenant", "")}-{{region}}")'
            },
        ),
        req,
        _config(named_values={"api-key": "k-1", "region": "eu"}),
    )

    assert req.headers["x-api-key"] == "k-1-acme"
    assert req.query["tenant"] == "acme-eu"
