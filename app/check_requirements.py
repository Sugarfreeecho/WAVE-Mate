"""
检查 requirements.txt 中列出的包是否已安装（pip show  distribution name）。
在 start_agent 启动前调用，避免缺依赖导致运行时才报错。

用法: python check_requirements.py
退出码: 0 全部已安装, 1 有缺失
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent
REQUIREMENTS_FILE = ROOT / "requirements.txt"


def _line_to_distribution_name(line: str) -> Optional[str]:
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    if "#" in s:
        s = s.split("#", 1)[0].strip()
    if not s:
        return None
    # 例如 openai>=1.0、markitdown[pptx]、ddgs>=9.0
    m = re.match(r"^([A-Za-z0-9._-]+)", s)
    if not m:
        return None
    return m.group(1).strip() or None


def load_required_distributions() -> List[str]:
    if not REQUIREMENTS_FILE.is_file():
        print(f"未找到 {REQUIREMENTS_FILE}", file=sys.stderr)
        return []
    out: List[str] = []
    for line in REQUIREMENTS_FILE.read_text(encoding="utf-8").splitlines():
        name = _line_to_distribution_name(line)
        if name and name not in out:
            out.append(name)
    return out


def pip_show_ok(dist: str) -> bool:
    r = subprocess.run(
        [sys.executable, "-m", "pip", "show", dist],
        capture_output=True,
    )
    return r.returncode == 0


def main() -> int:
    need = load_required_distributions()
    if not need:
        print("无依赖条目可检查，请确认 requirements.txt 存在且非空。", file=sys.stderr)
        return 1
    missing: List[str] = []
    for dist in need:
        if not pip_show_ok(dist):
            missing.append(dist)
    if missing:
        print("以下依赖未安装或不在当前环境:", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)
        print(file=sys.stderr)
        print('请执行: python -m pip install -r requirements.txt', file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
