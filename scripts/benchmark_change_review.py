"""Isolated, model-free measurement of review work between tool invocations.

Example: python scripts/benchmark_change_review.py --files 1000 --steps 20
The report separates first-capture initialization from steady-state work.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time


def load_store(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def measure(module, root: Path, workspace: Path, steps: int):
    rows = []
    store = module.FileChangeReviewStore(root)
    for index in range(steps + 1):
        started = time.perf_counter()
        pending = store.begin_capture("run_shell", {}, run_id="benchmark", tool_call_id=str(index), work_root=workspace)
        ready = time.perf_counter()
        # Exercise a real edit as well as unchanged scans, without executing a
        # shell or including its work in the review latency measurement.
        if index % 3 == 1:
            (workspace / "edited.txt").write_text(f"step {index}\n", encoding="utf-8")
        finished = time.perf_counter()
        store.finish_capture(pending)
        ended = time.perf_counter()
        rows.append({"before_ms": (ready - started) * 1000, "after_ms": (ended - finished) * 1000})
    # Actual boundary: post-tool audit of step N plus pre-tool audit of N+1.
    gaps = [rows[i]["after_ms"] + rows[i + 1]["before_ms"] for i in range(steps)]
    gaps.sort()
    return {"initial_before_ms": rows[0]["before_ms"], "p50_ms": statistics.median(gaps),
            "p95_ms": gaps[min(len(gaps) - 1, int(len(gaps) * .95))], "max_ms": max(gaps), "samples": rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--files", type=int, default=300)
    parser.add_argument("--file-kib", type=int, default=64)
    parser.add_argument("--steps", type=int, default=20)
    parser.add_argument("--target-ms", type=float, default=500)
    parser.add_argument("--baseline-store", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.files < 1 or args.steps < 1 or args.file_kib < 1:
        parser.error("files, steps and file-kib must be positive")
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="myagent-review-benchmark-") as temporary:
        sandbox = Path(temporary)
        workspace = sandbox / "repo"
        workspace.mkdir()
        subprocess.run(["git", "init", "-q", str(workspace)], check=True)
        for index in range(args.files):
            (workspace / f"{index:05}.bin").write_bytes(os.urandom(args.file_kib * 1024))
        (workspace / "edited.txt").write_text("initial\n", encoding="utf-8")
        report = {"files": args.files + 1, "payload_mib": args.files * args.file_kib / 1024,
                  "scope": "review after tool N + review before tool N+1; excludes model, tools and other ReAct work"}
        if args.baseline_store:
            previous = load_store(args.baseline_store, "benchmark_review_previous")
            report["previous"] = measure(previous, sandbox / "previous", workspace, args.steps)
        current = load_store(root / "plugins/change-review/store.py", "benchmark_review_current")
        report["current"] = measure(current, sandbox / "current", workspace, args.steps)
        report["target_ms"] = args.target_ms
        report["target_met"] = report["current"]["max_ms"] < args.target_ms
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({key: ({k: v for k, v in value.items() if k != "samples"} if isinstance(value, dict) else value)
                          for key, value in report.items()}, indent=2))
        return 0 if report["target_met"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
