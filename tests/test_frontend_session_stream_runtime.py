import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("script", [
    "frontend_session_stream_runtime.cjs",
    "ui_performance_runtime.cjs",
    "session_store_runtime.cjs",
    "new_session_lifecycle_runtime.cjs",
])
def test_frontend_session_stream_runtime(script):
    result = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / script)], cwd=ROOT,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
