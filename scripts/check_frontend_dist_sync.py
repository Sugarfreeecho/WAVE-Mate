#!/usr/bin/env python3
"""Verify frontend source and committed dist output are in sync.

The script builds the Vite frontend into a temporary directory and compares it
with app/templates/dist. It catches both missing builds after source edits and
manual dist edits that are not reproduced by the source build.
"""

from __future__ import annotations

import filecmp
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
DIST = ROOT / "app" / "templates" / "dist"


def _copy_tree_without_noise(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def _compare_dirs(left: Path, right: Path) -> list[str]:
    diffs: list[str] = []
    cmp = filecmp.dircmp(left, right)
    for name in cmp.left_only:
        diffs.append(f"only in expected build: {left / name}")
    for name in cmp.right_only:
        diffs.append(f"only in current dist: {right / name}")
    for name in cmp.diff_files:
        diffs.append(f"content differs: {right / name}")
    for name in cmp.funny_files:
        diffs.append(f"could not compare: {right / name}")
    for sub in cmp.common_dirs:
        diffs.extend(_compare_dirs(left / sub, right / sub))
    return diffs


def main() -> int:
    if not FRONTEND.exists():
        print(f"Missing frontend directory: {FRONTEND}", file=sys.stderr)
        return 2
    if not DIST.exists():
        print(f"Missing dist directory: {DIST}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="wavemate-dist-") as tmp:
        tmp_root = Path(tmp)
        expected_dist = tmp_root / "dist"
        backup_dist = tmp_root / "dist-current"

        _copy_tree_without_noise(DIST, backup_dist)

        env = os.environ.copy()
        env["WAVEMATE_DIST_DIR"] = str(expected_dist)
        cmd = ["npm.cmd" if os.name == "nt" else "npm", "run", "build"]
        proc = subprocess.run(cmd, cwd=FRONTEND, env=env, text=True)
        if proc.returncode != 0:
            return proc.returncode

        diffs = _compare_dirs(expected_dist, backup_dist)
        if diffs:
            print("Frontend dist is not in sync with frontend/src.")
            print("Run `npm run build` from frontend/ and commit the generated dist.")
            for item in diffs[:80]:
                print(f"- {item}")
            if len(diffs) > 80:
                print(f"- ... {len(diffs) - 80} more differences")
            return 1

    print("Frontend dist is in sync.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
