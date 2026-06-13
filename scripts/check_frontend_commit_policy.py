#!/usr/bin/env python3
"""Guard frontend source/dist consistency before committing or pushing.

This wrapper is intentionally small: it runs the reproducible dist check and,
when invoked inside a Git worktree, warns about suspicious staged changes such
as dist-only edits or source-only edits.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run(cmd: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)


def _staged_paths() -> list[str]:
    proc = _run(["git", "diff", "--cached", "--name-only"])
    if proc.returncode != 0:
        return []
    return [line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()]


def _warn_staged_shape(paths: list[str]) -> int:
    if not paths:
        return 0
    src_changed = any(p.startswith("frontend/src/") or p.startswith("frontend/index.html") for p in paths)
    dist_changed = any(p.startswith("app/templates/dist/") for p in paths)
    if src_changed and not dist_changed:
        print("Frontend source changed but app/templates/dist is not staged.", file=sys.stderr)
        print("Run `npm run build` from frontend/ and stage the generated dist.", file=sys.stderr)
        return 1
    if dist_changed and not src_changed:
        print("Frontend dist changed without matching frontend source changes.", file=sys.stderr)
        print("Do not hand-edit dist; make the source change under frontend/src and rebuild.", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    paths = _staged_paths()
    shape_rc = _warn_staged_shape(paths)
    dist_rc = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_frontend_dist_sync.py")], cwd=ROOT).returncode
    return shape_rc or dist_rc


if __name__ == "__main__":
    raise SystemExit(main())
