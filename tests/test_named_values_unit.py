"""Direct unit tests for named value resolution and secret masking.

Mutation testing put a number on why these are needed. mutmut's forked worker
crashed on 54 of this module's mutants, so it scored nothing at all for as long
as the crash went unexplained. Reconciled out of process, every one of those 54
turned out to be a survivor: the module sat at 52% while reading as covered,
because everything that reached it did so through the gateway, on the way to
asserting something else.

Masking is the part that matters most. A mutant surviving in `mask_secret_data`
means nothing in the suite would notice a secret being written out in the clear.
"""

from __future__ import annotations

import pytest

from app.config import GatewayConfig, KeyVaultNamedValueConfig, NamedValueConfig
from app.named_values import (
    mask_secret_data,
    mask_secret_text,
    named_value_env_var,
    resolve_named_value,
    resolve_named_values_in_text,
    secret_named_value_map,
)


def _config(**named_values: NamedValueConfig) -> GatewayConfig:
    return GatewayConfig(allow_anonymous=True, named_values=dict(named_values))


# --- the environment variable name -----------------------------------------


def test_the_env_var_name_is_the_prefixed_upper_cased_name() -> None:
    assert named_value_env_var("tenant") == "APIM_NAMED_VALUE_TENANT"


def test_runs_of_punctuation_collapse_to_one_underscore() -> None:
    assert named_value_env_var("api--key.v2") == "APIM_NAMED_VALUE_API_KEY_V2"


def test_leading_and_trailing_punctuation_is_dropped_not_kept_as_underscores() -> None:
    assert named_value_env_var("  -api-key-  ") == "APIM_NAMED_VALUE_API_KEY"


def test_a_name_with_nothing_usable_in_it_falls_back_to_the_bare_prefix() -> None:
    assert named_value_env_var("---") == "APIM_NAMED_VALUE"
    assert named_value_env_var("") == "APIM_NAMED_VALUE"


def test_digits_survive_the_normalisation() -> None:
    assert named_value_env_var("key1") == "APIM_NAMED_VALUE_KEY1"


def test_only_underscores_are_trimmed_from_the_normalised_name() -> None:
    """A letter at either end stays, however much it looks like padding.

    The trim runs before the upper-casing, so the letter that pins this has to be
    upper case in the name itself.
    """
    assert named_value_env_var("-X-api-X-") == "APIM_NAMED_VALUE_X_API_X"


# --- resolution order ------------------------------------------------------


def test_a_name_the_config_does_not_declare_resolves_to_nothing() -> None:
    assert resolve_named_value(_config(), "absent") is None


def test_a_configured_value_resolves_from_the_config() -> None:
    resolved = resolve_named_value(_config(tenant=NamedValueConfig(value="acme")), "tenant")

    assert resolved is not None
    assert resolved.name == "tenant"
    assert resolved.value == "acme"
    assert resolved.is_secret is False
    assert resolved.source == "config"
    assert resolved.env_var_name == "APIM_NAMED_VALUE_TENANT"


def test_a_configured_value_marked_secret_stays_secret() -> None:
    resolved = resolve_named_value(_config(token=NamedValueConfig(value="s3cret", secret=True)), "token")

    assert resolved is not None
    assert resolved.is_secret is True
    assert resolved.source == "config"


def test_the_environment_overrides_the_configured_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APIM_NAMED_VALUE_TENANT", "from-env")
    resolved = resolve_named_value(_config(tenant=NamedValueConfig(value="acme")), "tenant")

    assert resolved is not None
    assert resolved.name == "tenant"
    assert resolved.value == "from-env"
    assert resolved.source == "env"
    assert resolved.is_secret is False
    assert resolved.env_var_name == "APIM_NAMED_VALUE_TENANT"


def test_an_empty_environment_override_still_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only an unset variable defers to the config: an empty one is a value."""
    monkeypatch.setenv("APIM_NAMED_VALUE_TENANT", "")
    resolved = resolve_named_value(_config(tenant=NamedValueConfig(value="acme")), "tenant")

    assert resolved is not None
    assert resolved.value == ""
    assert resolved.source == "env"


def test_an_environment_override_of_a_secret_is_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APIM_NAMED_VALUE_TOKEN", "from-env")
    resolved = resolve_named_value(_config(token=NamedValueConfig(value="s3cret", secret=True)), "token")

    assert resolved is not None
    assert resolved.is_secret is True


def test_an_environment_override_of_a_key_vault_value_is_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APIM_NAMED_VALUE_TOKEN", "from-env")
    resolved = resolve_named_value(
        _config(token=NamedValueConfig(value_from_key_vault=KeyVaultNamedValueConfig(secret_id="https://kv/secret"))),
        "token",
    )

    assert resolved is not None
    assert resolved.value == "from-env"
    assert resolved.is_secret is True
    assert resolved.source == "env"


def test_a_key_vault_value_with_nothing_to_resolve_it_from_is_a_secret_with_no_value() -> None:
    resolved = resolve_named_value(
        _config(token=NamedValueConfig(value_from_key_vault=KeyVaultNamedValueConfig(secret_id="https://kv/secret"))),
        "token",
    )

    assert resolved is not None
    assert resolved.name == "token"
    assert resolved.value is None
    assert resolved.is_secret is True
    assert resolved.source == "key_vault"
    assert resolved.env_var_name == "APIM_NAMED_VALUE_TOKEN"


# --- substitution in text --------------------------------------------------


def test_a_named_value_is_substituted_into_text() -> None:
    cfg = _config(tenant=NamedValueConfig(value="acme"))

    assert resolve_named_values_in_text("https://{{tenant}}.example", cfg) == "https://acme.example"


def test_surrounding_whitespace_inside_the_braces_is_ignored() -> None:
    cfg = _config(tenant=NamedValueConfig(value="acme"))

    assert resolve_named_values_in_text("{{  tenant  }}", cfg) == "acme"


def test_every_occurrence_is_substituted() -> None:
    cfg = _config(a=NamedValueConfig(value="1"), b=NamedValueConfig(value="2"))

    assert resolve_named_values_in_text("{{a}}-{{b}}-{{a}}", cfg) == "1-2-1"


def test_an_unknown_or_unresolved_name_is_left_exactly_as_written() -> None:
    cfg = _config(
        token=NamedValueConfig(value_from_key_vault=KeyVaultNamedValueConfig(secret_id="https://kv/secret")),
    )

    assert resolve_named_values_in_text("{{absent}}", cfg) == "{{absent}}"
    assert resolve_named_values_in_text("{{token}}", cfg) == "{{token}}"


# --- which values count as secrets -----------------------------------------


def test_the_secret_map_holds_only_secrets_that_have_a_value() -> None:
    cfg = _config(
        plain=NamedValueConfig(value="visible"),
        token=NamedValueConfig(value="s3cret", secret=True),
        empty=NamedValueConfig(value="", secret=True),
        vaulted=NamedValueConfig(value_from_key_vault=KeyVaultNamedValueConfig(secret_id="https://kv/secret")),
    )

    assert secret_named_value_map(cfg) == {"token": "s3cret"}


def test_the_secret_map_follows_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APIM_NAMED_VALUE_TOKEN", "from-env")
    cfg = _config(token=NamedValueConfig(value="s3cret", secret=True))

    assert secret_named_value_map(cfg) == {"token": "from-env"}


# --- masking ---------------------------------------------------------------


def _secret_config() -> GatewayConfig:
    return _config(
        token=NamedValueConfig(value="s3cret", secret=True),
        other=NamedValueConfig(value="hunter2", secret=True),
        plain=NamedValueConfig(value="visible"),
    )


def test_every_secret_is_masked_wherever_it_appears_in_the_text() -> None:
    masked = mask_secret_text("s3cret and hunter2 and s3cret and visible", _secret_config())

    assert masked == "*** and *** and *** and visible"


def test_text_with_no_secret_in_it_is_unchanged() -> None:
    assert mask_secret_text("nothing here", _secret_config()) == "nothing here"


def test_masking_a_string_masks_its_secrets() -> None:
    assert mask_secret_data("token=s3cret", _secret_config()) == "token=***"


def test_masking_bytes_returns_masked_text() -> None:
    assert mask_secret_data(b"token=s3cret", _secret_config()) == "token=***"


def test_undecodable_bytes_are_replaced_rather_than_raising() -> None:
    # Accepted equivalents: mask_secret_data__mutmut_11 and __mutmut_14 spell the
    # encoding "UTF-8" or leave it to the default. Python resolves all three to
    # the same codec, so no input can tell them apart.
    assert mask_secret_data(b"\xffs3cret", _secret_config()) == "�***"


def test_masking_a_dict_masks_the_values_and_stringifies_the_keys() -> None:
    assert mask_secret_data({"a": "s3cret", 1: "hunter2"}, _secret_config()) == {"a": "***", "1": "***"}


def test_masking_a_list_masks_every_item() -> None:
    assert mask_secret_data(["s3cret", "clean"], _secret_config()) == ["***", "clean"]


def test_masking_reaches_secrets_nested_inside_lists_and_dicts() -> None:
    payload = {"outer": [{"inner": "s3cret"}, ["hunter2"]]}

    assert mask_secret_data(payload, _secret_config()) == {"outer": [{"inner": "***"}, ["***"]]}


def test_a_value_that_is_not_text_or_a_container_passes_through_unchanged() -> None:
    cfg = _secret_config()

    assert mask_secret_data(7, cfg) == 7
    assert mask_secret_data(None, cfg) is None
    assert mask_secret_data(True, cfg) is True
