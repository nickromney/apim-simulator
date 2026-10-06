"""Serial randomized cache-mode collection; synthetic in-memory requests only."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import random
import re
import resource
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONDITIONS = [(scenario, mode) for scenario in ("expressions", "versioned-routes") for mode in ("cached", "uncached")]


def schedule(seed=20261006):
    rng = random.Random(seed)
    result = []
    for phase, blocks in [("warmup", 3), ("measured", 10)]:
        for block in range(blocks):
            conditions = CONDITIONS.copy()
            rng.shuffle(conditions)
            result.extend(
                {"phase": phase, "block": block, "scenario": scenario, "mode": mode} for scenario, mode in conditions
            )
    return result


def host_sample():
    # n=0 prevents collection of other applications' process data.
    result = subprocess.run(
        ["/usr/bin/top", "-l", "2", "-s", "1", "-n", "0"], capture_output=True, text=True, check=True, timeout=10
    )
    lines = re.findall(r"CPU usage: ([\d.]+)% user, ([\d.]+)% sys, ([\d.]+)% idle", result.stdout)
    if not lines:
        raise RuntimeError("numeric macOS CPU sample unavailable")
    user, system, idle = map(float, lines[-1])
    return {
        "utc": datetime.now(UTC).isoformat(),
        "load": os.getloadavg(),
        "cpu_user_percent": user,
        "cpu_system_percent": system,
        "cpu_idle_percent": idle,
    }


def idle_gate(samples):
    idle = [row["cpu_idle_percent"] for row in samples]
    return min(idle) >= 85 and max(idle) - min(idle) <= 10


def fingerprint():
    files = [
        "scripts/collect_cache_profiles.py",
        "scripts/profile_cache_modes.py",
        "scripts/benchmark_cache_modes.py",
        "scripts/benchmark_gateway.py",
        "app/config.py",
        "app/apim_expr.py",
        "app/proxy.py",
        "uv.lock",
    ]
    return {
        "utc": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "python": sys.version,
        "source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "logical_cpus": os.cpu_count(),
        "hardware": subprocess.check_output(
            ["sysctl", "-n", "hw.model", "hw.physicalcpu", "hw.logicalcpu", "hw.memsize"], text=True
        ).splitlines(),
        "filesystem": subprocess.check_output(["stat", "-f", "%T", str(ROOT)], text=True).strip(),
        "power": subprocess.check_output(["pmset", "-g", "batt"], text=True).strip(),
        "isolation": "serial local processes; no affinity, power or OS tuning; numeric boundary host CPU samples",
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files},
    }


def command(condition, output):
    args = [
        "uv",
        "run",
        "--frozen",
        "--offline",
        "--extra",
        "dev",
        "python",
        "scripts/benchmark_cache_modes.py",
        "--cache-mode",
        condition["mode"],
        "--scenario",
        condition["scenario"],
        "--requests",
        "1000",
        "--routes",
        "100",
        "--output",
        str(output),
    ]
    if condition["scenario"] == "versioned-routes":
        args.append("--varying-paths")
    return args


def collect(output, allow_shared_host=False, seed=20261006):
    output.mkdir(parents=True, exist_ok=False)
    (output / "fingerprint.json").write_text(json.dumps(fingerprint(), indent=2) + "\n")
    preflight = [host_sample() for _ in range(5)]
    stable = idle_gate(preflight)
    (output / "preflight.json").write_text(
        json.dumps({"samples": preflight, "idle_gate_passed": stable, "allow_shared_host": allow_shared_host}, indent=2)
        + "\n"
    )
    if not stable and not allow_shared_host:
        raise RuntimeError("host idle gate failed; no workload measurements collected")
    rows = []
    for index, condition in enumerate(schedule(seed)):
        destination = output / f"run-{index:02d}.json"
        before = host_sample()
        if not idle_gate([*preflight, before]) and not allow_shared_host:
            (output / "rejection.json").write_text(
                json.dumps({"sequence": index, "stage": "before", "sample": before}, indent=2) + "\n"
            )
            raise RuntimeError("host boundary idle gate failed; next workload not launched")
        usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        start = time.perf_counter()
        subprocess.run(command(condition, destination), cwd=ROOT, stdout=subprocess.DEVNULL, check=True, timeout=60)
        wall = time.perf_counter() - start
        after_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        cpu = after_usage.ru_utime + after_usage.ru_stime - usage.ru_utime - usage.ru_stime
        after = host_sample()
        result = json.loads(destination.read_text())
        assert result["cache_mode"] == condition["mode"] and result["scenario"] == condition["scenario"]
        assert result["requests"] == 1000 and result["varying_paths"] == (condition["scenario"] == "versioned-routes")
        rows.append(
            {
                **condition,
                "sequence": index,
                "result": result,
                "host_before": before,
                "host_after": after,
                "process_wall_seconds": wall,
                "process_cpu_seconds": cpu,
                "process_cpu_percent": cpu / wall * 100,
            }
        )
        (output / "runs.json").write_text(json.dumps(rows, indent=2) + "\n")
        if not idle_gate([*preflight, before, after]) and not allow_shared_host:
            (output / "rejection.json").write_text(
                json.dumps({"sequence": index, "stage": "after", "sample": after}, indent=2) + "\n"
            )
            raise RuntimeError("host boundary idle gate failed; row preserved, collection rejected")
        print(f"{index + 1}/52 {condition['phase']} {condition['scenario']} {condition['mode']}", flush=True)
    summary = {}
    for scenario, mode in CONDITIONS:
        group = [
            row for row in rows if row["scenario"] == scenario and row["mode"] == mode and row["phase"] == "measured"
        ]
        assert len(group) == 10
        metrics = {
            key: statistics.median(row["result"][key] for row in group)
            for key in ["requests_per_second", "p50_ms", "p95_ms", "p99_ms", "peak_rss_mib"]
        }
        throughput = [row["result"]["requests_per_second"] for row in group]
        metrics.update(
            throughput_cv=statistics.stdev(throughput) / statistics.mean(throughput),
            host_idle_min=min(row[key]["cpu_idle_percent"] for row in group for key in ["host_before", "host_after"]),
            child_cpu_percent_median=statistics.median(row["process_cpu_percent"] for row in group),
        )
        summary[f"{scenario}-{mode}"] = metrics
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--allow-shared-host",
        action="store_true",
        help="Explicitly collect qualified shared-host observations after a failed idle gate; never causal evidence",
    )
    args = parser.parse_args()
    collect(args.output.resolve(), args.allow_shared_host)


if __name__ == "__main__":
    main()
