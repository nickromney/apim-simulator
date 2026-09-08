from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import pytest
import yaml

CONTRACT_MATRIX_PATH = Path(__file__).resolve().parent.parent / "contracts" / "contract_matrix.yml"
ENFORCED_STATUSES = {"supported", "adapted", "partial"}


def _string_list_field(entry: dict[str, Any], field: str, contract_id: str) -> tuple[list[str] | None, str | None]:
    """Read a list-of-non-empty-strings field. Returns (value, error message)."""
    raw = entry.get(field) or []
    if not isinstance(raw, list) or not all(isinstance(item, str) and item.strip() for item in raw):
        return None, f"{contract_id}: {field} must be a list of non-empty strings"
    return [item.strip() for item in raw], None


def _parse_contract_entry(
    entry: Any, index: int, seen: set[str]
) -> tuple[str | None, dict[str, Any] | None, str | None]:
    """Validate one matrix entry. Returns (id, parsed, error message)."""
    if not isinstance(entry, dict):
        return None, None, f"contracts[{index}] must be a mapping"

    contract_id = entry.get("id")
    if not isinstance(contract_id, str) or not contract_id.strip():
        return None, None, f"contracts[{index}] is missing a non-empty string 'id'"
    contract_id = contract_id.strip()

    if contract_id in seen:
        return None, None, f"duplicate contract id: {contract_id}"

    owner_tests, error = _string_list_field(entry, "owner_tests", contract_id)
    if error is not None:
        return None, None, error
    doc_refs, error = _string_list_field(entry, "doc_refs", contract_id)
    if error is not None:
        return None, None, error

    return (
        contract_id,
        {
            "status": str(entry.get("status") or "").strip().lower(),
            "owner_tests": owner_tests,
            "doc_refs": doc_refs,
        },
        None,
    )


def _load_contract_matrix() -> dict[str, dict[str, Any]]:
    """Read and validate contracts/contract_matrix.yml.

    Every entry is checked, and all failures are reported together: fixing one
    malformed contract at a time across repeated runs is the slow way to do it.
    """
    if not CONTRACT_MATRIX_PATH.exists():
        raise pytest.UsageError(f"Contract matrix not found: {CONTRACT_MATRIX_PATH}")

    payload = yaml.safe_load(CONTRACT_MATRIX_PATH.read_text(encoding="utf-8")) or {}
    contracts = payload.get("contracts")
    if not isinstance(contracts, list):
        raise pytest.UsageError("contracts/contract_matrix.yml must define a top-level 'contracts' list")

    out: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for index, entry in enumerate(contracts, start=1):
        contract_id, parsed, error = _parse_contract_entry(entry, index, set(out))
        if error is not None:
            errors.append(error)
            continue
        out[contract_id] = parsed

    if errors:
        raise pytest.UsageError("Contract matrix validation failed:\n- " + "\n- ".join(errors))

    return out


def _iter_contract_ids(item: pytest.Item) -> set[str]:
    contract_ids: set[str] = set()
    for mark in item.iter_markers("contract"):
        if not mark.args:
            raise pytest.UsageError(f"{item.nodeid}: contract marker must include at least one contract id")
        for arg in mark.args:
            if not isinstance(arg, str) or not arg.strip():
                raise pytest.UsageError(f"{item.nodeid}: contract marker args must be non-empty strings")
            contract_ids.add(arg.strip())
    return contract_ids


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "contract(*ids): associates a test with one or more contract ids from contracts/contract_matrix.yml",
    )
    config._contract_matrix = _load_contract_matrix()


def _is_partial_run(config: pytest.Config) -> bool:
    """True when a subset of the suite was selected (paths, -k, or -m).

    Contract coverage can only be judged against the full suite; enforcing it
    on partial runs would make single-file invocations impossible.
    """
    if config.getoption("keyword", "") or config.getoption("markexpr", ""):
        return True
    testpaths = set(config.getini("testpaths") or [])
    return any(arg.split("::")[0] not in testpaths for arg in config.args)


def _collect_contract_marks(
    session: pytest.Session, contracts: dict[str, dict[str, Any]]
) -> tuple[dict[str, set[str]], dict[str, set[str]], list[str]]:
    """Index which collected tests claim which contract ids.

    Returns the tests per contract, the contracts per test, and any test naming
    a contract id the matrix does not define.
    """
    marked_items: dict[str, set[str]] = {contract_id: set() for contract_id in contracts}
    contract_ids_by_nodeid: dict[str, set[str]] = {}
    errors: list[str] = []

    for item in session.items:
        item_contract_ids = _iter_contract_ids(item)
        if not item_contract_ids:
            continue
        contract_ids_by_nodeid[item.nodeid] = item_contract_ids
        for contract_id in item_contract_ids:
            if contract_id not in contracts:
                errors.append(f"{item.nodeid}: unknown contract id {contract_id!r}")
                continue
            marked_items[contract_id].add(item.nodeid)

    return marked_items, contract_ids_by_nodeid, errors


def _owner_test_errors(
    session: pytest.Session,
    contract_id: str,
    owner_patterns: list[str],
    contract_ids_by_nodeid: dict[str, set[str]],
) -> list[str]:
    """Check that each declared owner pattern matches a test that claims the contract.

    A pattern matching nothing and a pattern matching only unmarked tests are
    different failures, and both are worth naming separately.
    """
    errors: list[str] = []
    for pattern in owner_patterns:
        matched_nodeids = [item.nodeid for item in session.items if fnmatch(item.nodeid, pattern)]
        if not matched_nodeids:
            errors.append(f"{contract_id}: owner test pattern {pattern!r} matched no collected tests")
            continue
        if not any(contract_id in contract_ids_by_nodeid.get(nodeid, set()) for nodeid in matched_nodeids):
            errors.append(
                f"{contract_id}: owner test pattern {pattern!r} matched tests, but none were marked with {contract_id}"
            )
    return errors


def _contract_coverage_errors(
    session: pytest.Session,
    contracts: dict[str, dict[str, Any]],
    marked_items: dict[str, set[str]],
    contract_ids_by_nodeid: dict[str, set[str]],
) -> list[str]:
    """Every enforced contract needs a marked test and a working owner pattern."""
    errors: list[str] = []
    for contract_id, metadata in contracts.items():
        if metadata["status"] not in ENFORCED_STATUSES:
            continue
        if not marked_items[contract_id]:
            errors.append(f"{contract_id}: no collected tests are marked with this contract id")

        owner_patterns = metadata["owner_tests"]
        if not owner_patterns:
            errors.append(f"{contract_id}: enforced contracts must declare at least one owner_tests entry")
            continue
        errors.extend(_owner_test_errors(session, contract_id, owner_patterns, contract_ids_by_nodeid))
    return errors


def _fail_on(errors: list[str]) -> None:
    if errors:
        raise pytest.UsageError("Contract coverage validation failed:\n- " + "\n- ".join(errors))


def pytest_collection_finish(session: pytest.Session) -> None:
    """Check the collected suite against the contract matrix."""
    contracts: dict[str, dict[str, Any]] = getattr(session.config, "_contract_matrix", {})
    marked_items, contract_ids_by_nodeid, errors = _collect_contract_marks(session, contracts)

    # Unknown-id errors apply to every run; coverage checks need the full suite.
    if _is_partial_run(session.config):
        _fail_on(errors)
        return

    errors.extend(_contract_coverage_errors(session, contracts, marked_items, contract_ids_by_nodeid))
    _fail_on(errors)
