"""Collect separate source CPU/allocation evidence, excluded from timing tables."""

from __future__ import annotations

import argparse
import json
import pstats
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def collect(output):
    from scripts.collect_cache_profiles import CONDITIONS, command, host_sample

    output.mkdir(parents=True, exist_ok=False)
    conditions = CONDITIONS.copy()
    random.Random(20261006).shuffle(conditions)
    for scenario, mode in conditions:
        name = f"{scenario}-{mode}"
        condition = {"scenario": scenario, "mode": mode}
        profile = output / f"{name}.pstats"
        before = host_sample()
        subprocess.run(
            command(condition, output / f"{name}-profile-run.json") + ["--profile", str(profile)],
            cwd=ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
            timeout=60,
        )
        after = host_sample()
        rows = []
        for (filename, line, function), (primitive, calls, self_time, cumulative, _) in pstats.Stats(
            str(profile)
        ).stats.items():
            path = Path(filename)
            if path.is_relative_to(ROOT / "app"):
                rows.append(
                    {
                        "file": path.relative_to(ROOT).as_posix(),
                        "line": line,
                        "function": function,
                        "primitive_calls": primitive,
                        "calls": calls,
                        "self_seconds": self_time,
                        "cumulative_seconds": cumulative,
                    }
                )
        (output / f"{name}-source-cpu.json").write_text(
            json.dumps(
                {
                    "scope": "cProfile measured request loop after20 internal warmups; cumulative parents overlap; coroutine resumptions affect counts",
                    "host_before": before,
                    "host_after": after,
                    "all_source_functions": rows,
                    "ranked_self": sorted(rows, key=lambda row: row["self_seconds"], reverse=True)[:20],
                    "ranked_cumulative": sorted(rows, key=lambda row: row["cumulative_seconds"], reverse=True)[:20],
                },
                indent=2,
            )
            + "\n"
        )
        subprocess.run(
            command(condition, output / f"{name}-allocation-run.json")
            + ["--allocation-report", str(output / f"{name}-allocations.json")],
            cwd=ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
            timeout=60,
        )
        print(f"Profile/allocation complete: {name}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    collect(args.output.resolve())
