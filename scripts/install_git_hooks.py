#!/usr/bin/env python3
"""Install local Git hooks for WAVE-Mate development."""

from __future__ import annotations

import os
import stat
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".git" / "hooks"

HOOK_BODY = """#!/bin/sh
python scripts/check_frontend_commit_policy.py
"""


def main() -> int:
    if not (ROOT / ".git").exists():
        print("Not inside the WAVE-Mate Git worktree.")
        return 2
    HOOKS.mkdir(parents=True, exist_ok=True)
    for name in ("pre-commit", "pre-push"):
        path = HOOKS / name
        path.write_text(HOOK_BODY, encoding="utf-8")
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print(f"Installed {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
