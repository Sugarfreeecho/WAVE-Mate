"""Web UI 启动入口。依赖自检请运行 check_requirements.bat / check_requirements.py。"""

from __future__ import annotations

import ssl_bypass  # SSL certificate bypass

import os
import socket
import threading
import time
import webbrowser
from contextlib import asynccontextmanager

import uvicorn


def _env_wants_browser() -> bool:
    v = os.getenv("OPEN_BROWSER", "True").strip().lower()
    return v not in ("0", "false", "no", "off")


def _open_browser_when_listening(host: str, port: int) -> None:
    url = f"http://{host}:{port}/"
    deadline = time.monotonic() + 120.0
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                pass
        except OSError:
            time.sleep(0.15)
            continue
        try:
            webbrowser.open(url, new=0, autoraise=True)
        except TypeError:
            webbrowser.open(url, new=0)
        return


def _schedule_browser_open(host: str, port: int) -> None:
    if not _env_wants_browser():
        return
    threading.Thread(
        target=_open_browser_when_listening,
        args=(host, port),
        daemon=True,
    ).start()


if __name__ == "__main__":
    from webui import fastapi_app
    from agent_harness import refresh_executor_client_from_env
    
    # 确保配置正确加载，避免重启后400/401错误
    refresh_executor_client_from_env()

    _listen_host = "127.0.0.1"
    _listen_port = 8192

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 启动时打开浏览器
        _schedule_browser_open("127.0.0.1", _listen_port)
        yield
        # 关闭时不需要特殊清理
        
    fastapi_app.router.lifespan_context = lifespan

    uvicorn.run(fastapi_app, host=_listen_host, port=_listen_port)
