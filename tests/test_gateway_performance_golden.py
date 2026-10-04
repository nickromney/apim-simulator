"""Keep expression values, errors, policy traces, and bodies byte-equivalent."""

import sys
from pathlib import Path

from scripts.golden_policy_outputs import main as policy_outputs
from scripts.golden_route_outputs import main as route_outputs


def test_policy_expression_golden_outputs(tmp_path, monkeypatch) -> None:
    output = tmp_path / "policy_outputs.json"
    monkeypatch.setattr(sys, "argv", ["golden_policy_outputs", "--output", str(output)])
    policy_outputs()
    assert output.read_bytes() == Path("tests/fixtures/performance/policy_outputs.json").read_bytes()


def test_routing_golden_outputs(tmp_path, monkeypatch) -> None:
    output = tmp_path / "route_outputs.json"
    monkeypatch.setattr(sys, "argv", ["golden_route_outputs", "--output", str(output)])
    route_outputs()
    assert output.read_bytes() == Path("tests/fixtures/performance/route_outputs.json").read_bytes()
