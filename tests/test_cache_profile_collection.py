"""Collector fairness checks, without running or timing a workload."""

from collections import Counter
from pathlib import Path

from scripts.collect_cache_profiles import CONDITIONS, command, idle_gate, schedule


def test_seeded_schedule_balances_randomized_blocks():
    rows = schedule()
    assert rows == schedule()
    assert rows != schedule(123)
    assert len(rows) == 52
    assert Counter((row["scenario"], row["mode"], row["phase"]) for row in rows) == {
        (scenario, mode, phase): count
        for scenario, mode in CONDITIONS
        for phase, count in [("warmup", 3), ("measured", 10)]
    }
    for offset in range(0, len(rows), 4):
        assert {(row["scenario"], row["mode"]) for row in rows[offset : offset + 4]} == set(CONDITIONS)


def test_cache_conditions_share_inputs_and_only_change_mode():
    for scenario in ["expressions", "versioned-routes"]:
        cached = command({"scenario": scenario, "mode": "cached"}, Path("result.json"))
        uncached = command({"scenario": scenario, "mode": "uncached"}, Path("result.json"))
        assert [part.replace("uncached", "cached") for part in uncached] == cached
        assert ("--varying-paths" in cached) == (scenario == "versioned-routes")
        assert cached[cached.index("--requests") + 1] == "1000"
        assert cached[cached.index("--routes") + 1] == "100"


def test_idle_gate_keeps_predeclared_floor_and_spread():
    def samples(*values):
        return [{"cpu_idle_percent": value} for value in values]

    assert idle_gate(samples(85, 95))
    assert not idle_gate(samples(84.99, 90))
    assert not idle_gate(samples(85, 95.01))


def test_failed_boundary_prevents_workload(monkeypatch, tmp_path):
    import json

    import pytest

    from scripts import collect_cache_profiles as collector

    samples = iter([{"cpu_idle_percent": 90}] * 5 + [{"cpu_idle_percent": 84}])
    monkeypatch.setattr(collector, "host_sample", lambda: next(samples))
    monkeypatch.setattr(collector, "fingerprint", lambda: {})
    monkeypatch.setattr(collector.subprocess, "run", lambda *args, **kwargs: pytest.fail("workload launched"))
    output = tmp_path / "evidence"
    with pytest.raises(RuntimeError, match="next workload not launched"):
        collector.collect(output)
    assert json.loads((output / "rejection.json").read_text())["stage"] == "before"
    assert not (output / "runs.json").exists()


def test_failed_after_boundary_preserves_row_and_rejects_summary(monkeypatch, tmp_path):
    import json

    import pytest

    from scripts import collect_cache_profiles as collector

    samples = iter([{"cpu_idle_percent": 90}] * 6 + [{"cpu_idle_percent": 84}])
    monkeypatch.setattr(collector, "host_sample", lambda: next(samples))
    monkeypatch.setattr(collector, "fingerprint", lambda: {})

    def workload(args, **kwargs):
        result = {
            "cache_mode": args[args.index("--cache-mode") + 1],
            "scenario": args[args.index("--scenario") + 1],
            "requests": 1000,
            "varying_paths": "--varying-paths" in args,
        }
        Path(args[args.index("--output") + 1]).write_text(json.dumps(result))

    monkeypatch.setattr(collector.subprocess, "run", workload)
    output = tmp_path / "evidence"
    with pytest.raises(RuntimeError, match="row preserved"):
        collector.collect(output)
    assert len(json.loads((output / "runs.json").read_text())) == 1
    assert json.loads((output / "rejection.json").read_text())["stage"] == "after"
    assert not (output / "summary.json").exists()
