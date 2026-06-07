"""
MCP（Model Context Protocol）桥：支持 stdio / SSE / Streamable HTTP，配置变更热重载，
高危调用可走 Web UI 审批（见 MCP_UI_APPROVAL），结构化日志。

配置：`PROJECT_ROOT/mcp_servers.json` 或 `MCP_SERVERS_JSON`；路径可用 `MCP_SERVERS_PATH`。
禁用：`MCP_ENABLED=0`；未安装 `mcp` 包时跳过。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Tuple

import httpx

from agent_harness import PROJECT_ROOT

logger = logging.getLogger(__name__)

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.sse import sse_client
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared._httpx_utils import create_mcp_http_client

    _MCP_IMPORT_OK = True
except ImportError:
    ClientSession = None  # type: ignore
    StdioServerParameters = None  # type: ignore
    stdio_client = None  # type: ignore
    sse_client = None  # type: ignore
    streamable_http_client = None  # type: ignore
    create_mcp_http_client = None  # type: ignore
    _MCP_IMPORT_OK = False

_TOOL_NAME_SAFE = re.compile(r"[^a-zA-Z0-9_-]+")
_STOP = object()

_fname_to_tool: Dict[str, Tuple[str, str]] = {}
_servers: Dict[str, Any] = {}
_defs_snapshot: List[Dict[str, Any]] = []
_start_lock = asyncio.Lock()
_loaded_signature: Optional[str] = None
_last_config_error: Optional[str] = None


def _register_tools_globally(alias: str, tools: List[Any]) -> int:
    """Register a server's latest tools in the global OpenAI tool snapshots."""
    global _defs_snapshot
    seen_fname: set[str] = set()
    registered = 0
    for t in tools or []:
        orig_name = getattr(t, "name", "") or ""
        if not orig_name:
            continue
        desc = getattr(t, "description", "") or ""
        schema = getattr(t, "inputSchema", None)
        od = _openai_tool_def(alias, orig_name, desc, schema)
        fname = od["function"]["name"]
        if fname in seen_fname:
            continue
        seen_fname.add(fname)
        existing = _fname_to_tool.get(fname)
        if existing and existing != (alias, orig_name):
            logger.warning("MCP: duplicate tool key `%s`, skip `%s.%s`", fname, alias, orig_name)
            continue
        _fname_to_tool[fname] = (alias, orig_name)
        _defs_snapshot = [
            d
            for d in _defs_snapshot
            if d.get("function", {}).get("name") != fname
        ]
        _defs_snapshot.append(od)
        registered += 1
    return registered


def _enabled_flag() -> bool:
    v = (os.getenv("MCP_ENABLED") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _config_path() -> Path:
    custom = (os.getenv("MCP_SERVERS_PATH") or "").strip()
    if custom:
        return Path(custom).expanduser()
    return (PROJECT_ROOT / "mcp_servers.json").resolve()


def get_config_path() -> Path:
    """配置文件路径（供 Web UI 展示与保存）。"""
    return _config_path()


def _compute_config_signature() -> str:
    """配置签名：变更则触发关闭并重连。"""
    if not _enabled_flag():
        return "disabled"
    inline = (os.getenv("MCP_SERVERS_JSON") or "").strip()
    if inline:
        return "inline:" + hashlib.sha256(inline.encode("utf-8")).hexdigest()
    path = _config_path()
    try:
        st = path.stat()
        return f"file:{path.resolve()}:{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        return f"missing:{path.resolve()}"


def _load_servers_dict_from_config() -> Tuple[Optional[dict], Optional[str]]:
    """返回 (servers dict | None, error reason | None)。"""
    global _last_config_error
    _last_config_error = None
    if not _enabled_flag():
        return None, None
    inline = (os.getenv("MCP_SERVERS_JSON") or "").strip()
    raw_obj: Any = None
    if inline:
        try:
            raw_obj = json.loads(inline)
        except json.JSONDecodeError as e:
            _last_config_error = f"MCP_SERVERS_JSON parse error: {e}"
            logger.warning(_last_config_error)
            return None, _last_config_error
    else:
        path = _config_path()
        if not path.is_file():
            return None, None
        try:
            raw_obj = json.loads(path.read_text(encoding="utf-8"))
        except OSError as e:
            _last_config_error = f"read {path}: {e}"
            logger.warning(_last_config_error)
            return None, _last_config_error
        except json.JSONDecodeError as e:
            _last_config_error = f"{path}: {e}"
            logger.warning(_last_config_error)
            return None, _last_config_error

    if not isinstance(raw_obj, dict):
        _last_config_error = "MCP config root must be an object"
        logger.warning(_last_config_error)
        return None, _last_config_error
    if raw_obj.get("enabled") is False:
        return None, None
    servers = raw_obj.get("servers")
    if servers is None:
        servers = raw_obj.get("mcpServers")
    if not isinstance(servers, dict) or not servers:
        return None, None
    return servers, None


def _resolve_transport(cfg: dict) -> str:
    t = (cfg.get("transport") or "").strip().lower()
    url = str(cfg.get("url") or "").strip()
    cmd = str(cfg.get("command") or "").strip()
    if t in ("stdio", "sse", "streamable-http", "streamable_http", "http"):
        if t in ("http", "streamable_http"):
            return "streamable-http"
        return t
    if cmd:
        return "stdio"
    if url:
        return "streamable-http"
    return ""


def _safe_function_key(alias: str, tool_name: str) -> str:
    a = _TOOL_NAME_SAFE.sub("_", alias).strip("_").lower() or "srv"
    t = _TOOL_NAME_SAFE.sub("_", tool_name).strip("_").lower() or "tool"
    base = f"mcp_{a}_{t}"
    if len(base) > 120:
        base = base[:120].rstrip("_")
    return base


def _schema_to_parameters(schema: Any) -> Dict[str, Any]:
    if isinstance(schema, dict):
        return schema
    return {"type": "object", "properties": {}}


def _openai_tool_def(alias: str, name: str, description: str, input_schema: Any) -> Dict[str, Any]:
    fname = _safe_function_key(alias, name)
    desc = (description or "").strip() or name
    full_desc = f"[MCP server `{alias}`] {desc}"
    return {
        "type": "function",
        "function": {
            "name": fname,
            "description": full_desc[:4096],
            "parameters": _schema_to_parameters(input_schema),
        },
    }


def _serialize_call_tool_result_for_log(result: Any, max_len: int = 12000) -> str:
    try:
        if hasattr(result, "model_dump"):
            dumped = result.model_dump(mode="json")
            s = json.dumps(dumped, ensure_ascii=False, default=str)
            return s if len(s) <= max_len else s[:max_len] + "…[truncated]"
    except Exception:
        pass
    try:
        s = json.dumps(result, ensure_ascii=False, default=str)
        return s if len(s) <= max_len else s[:max_len] + "…[truncated]"
    except Exception:
        pass
    r = repr(result)
    return r if len(r) <= max_len else r[:max_len] + "…[truncated]"


def ui_approval_spec_for_mcp_tool(tool_name: str, tool_args: Any) -> Optional[Dict[str, str]]:
    """返回与 `_tool_ui_approval_spec` 相同形状的 dict；不需要审批时返回 None。"""
    if not tool_name.startswith("mcp_"):
        return None
    if (os.getenv("MCP_UI_APPROVAL") or "1").strip().lower() in ("0", "false", "no", "off"):
        return None
    allow_rx = (os.getenv("MCP_UI_APPROVAL_ALLOW_REGEX") or "").strip()
    if allow_rx:
        try:
            if re.search(allow_rx, tool_name):
                return None
        except re.error:
            logger.warning("MCP_UI_APPROVAL_ALLOW_REGEX invalid, ignored")
    pair = _fname_to_tool.get(tool_name)
    alias, orig = pair if pair else ("?", "?")
    try:
        args_preview = json.dumps(tool_args, ensure_ascii=False)[:1200] if isinstance(tool_args, dict) else str(tool_args)[:1200]
    except Exception:
        args_preview = str(tool_args)[:1200]
    return {
        "title": "MCP 工具确认",
        "message": "即将通过 MCP 调用外部工具。\n\n"
        f"服务器别名：`{alias}`\n"
        f"工具：`{orig}`\n\n"
        f"参数预览：\n{args_preview}",
        "subtitle": tool_name,
        "brief": f"MCP {alias}/{orig}",
    }


class _PersistentMcpServer:
    """通用持久会话：由 connect_cm 提供 (read, write) 流。"""

    def __init__(self, alias: str, transport_label: str, connect_cm: Callable[[], Any]):
        self.alias = alias
        self.transport_label = transport_label
        self._connect_cm = connect_cm
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None
        self._ready = asyncio.Event()
        self._tools: List[Any] = []
        self._fatal: Optional[str] = None
        self._restart_lock = asyncio.Lock()

    async def start(self, timeout_sec: float = 60.0) -> None:
        self._ready.clear()
        self._fatal = None
        self._task = asyncio.create_task(self._runner())
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            self._fatal = f"MCP `{self.alias}` ({self.transport_label}) startup timed out after {timeout_sec}s"
            raise RuntimeError(self._fatal) from None
        if self._fatal:
            raise RuntimeError(self._fatal)

    async def _runner(self) -> None:
        if not _MCP_IMPORT_OK or ClientSession is None:
            self._fatal = "Python package `mcp` is not installed"
            if not self._ready.is_set():
                self._ready.set()
            return
        try:
            async with self._connect_cm() as streams:
                read, write = streams[0], streams[1]
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    self._tools = list(listed.tools)
                    mapped = _register_tools_globally(self.alias, self._tools)
                    if not self._ready.is_set():
                        self._ready.set()
                    logger.info(
                        "MCP server ready alias=%s transport=%s tools=%s mapped_tools=%s",
                        self.alias,
                        self.transport_label,
                        len(self._tools),
                        mapped,
                    )
                    while True:
                        req = await self._queue.get()
                        if req is _STOP:
                            break
                        fut: asyncio.Future = req[0]
                        tname: str = req[1]
                        targs: Dict[str, Any] = req[2]
                        try:
                            result = await session.call_tool(tname, targs)
                            if not fut.done():
                                fut.set_result(result)
                        except BaseException as e:
                            if not fut.done():
                                fut.set_exception(e)
        except asyncio.CancelledError:
            logger.info("MCP server `%s` (%s) runner cancelled", self.alias, self.transport_label)
        except BaseException as e:
            self._fatal = str(e)
            logger.exception("MCP server `%s` (%s) exited", self.alias, self.transport_label)
        finally:
            self._fail_queued_requests()
            if not self._ready.is_set():
                self._ready.set()

    def _fail_queued_requests(self) -> None:
        err = self._fatal or f"MCP server `{self.alias}` is not connected"
        while True:
            try:
                req = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if req is _STOP:
                continue
            try:
                fut = req[0]
                if not fut.done():
                    fut.set_exception(RuntimeError(err))
            except Exception:
                pass

    async def _restart(self, retries: int = 3) -> None:
        async with self._restart_lock:
            if self._task is not None and not self._task.done() and not self._fatal:
                return
            last_err: Optional[BaseException] = None
            for attempt in range(1, max(1, retries) + 1):
                self._fatal = None
                self._ready.clear()
                self._task = asyncio.create_task(self._runner())
                try:
                    await asyncio.wait_for(self._ready.wait(), timeout=60.0)
                    if not self._fatal:
                        logger.info(
                            "MCP server `%s` restarted attempt=%s",
                            self.alias,
                            attempt,
                        )
                        return
                    last_err = RuntimeError(self._fatal)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    last_err = e
                if self._task and not self._task.done():
                    self._task.cancel()
                await asyncio.sleep(min(0.5 * attempt, 2.0))
            if last_err:
                raise RuntimeError(f"MCP server `{self.alias}` restart failed: {last_err}") from last_err
            raise RuntimeError(f"MCP server `{self.alias}` restart failed")

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        if self._task is None or self._task.done():
            await self._restart()
        if self._fatal:
            raise RuntimeError(self._fatal)
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        await self._queue.put((fut, tool_name, dict(arguments or {})))
        return await fut

    async def stop(self) -> None:
        if self._task is None:
            return
        await self._queue.put(_STOP)
        try:
            await asyncio.wait_for(self._task, timeout=20.0)
        except asyncio.TimeoutError:
            self._task.cancel()


def _make_stdio_connector(alias: str, cfg: dict) -> _PersistentMcpServer:
    cmd = str(cfg.get("command") or "").strip()
    args = cfg.get("args") or []
    if not isinstance(args, list):
        args = []
    args = [str(a) for a in args]
    env = cfg.get("env")
    env_d: Optional[Dict[str, str]] = None
    if isinstance(env, dict):
        env_d = {str(k): str(v) for k, v in env.items()}
    cwd = cfg.get("cwd") or cfg.get("workingDirectory")
    cwd_s = str(cwd).strip() if cwd else None
    params = StdioServerParameters(command=cmd, args=args, env=env_d, cwd=cwd_s or None)

    @asynccontextmanager
    async def _cm() -> AsyncIterator[Tuple[Any, Any]]:
        async with stdio_client(params) as rw:
            yield rw

    return _PersistentMcpServer(alias, "stdio", _cm)


def _make_sse_connector(alias: str, cfg: dict) -> _PersistentMcpServer:
    url = str(cfg.get("url") or "").strip()
    headers_raw = cfg.get("headers")
    headers: Optional[Dict[str, str]] = None
    if isinstance(headers_raw, dict):
        headers = {str(k): str(v) for k, v in headers_raw.items()}
    timeout = float(cfg.get("timeout", 30))
    sse_read = float(cfg.get("sse_read_timeout", cfg.get("sseReadTimeout", 300)))
    verify = cfg.get("verify", True)
    skip_verify = verify is False

    @asynccontextmanager
    async def _cm() -> AsyncIterator[Tuple[Any, Any]]:
        httpx_client_factory = create_mcp_http_client
        if skip_verify:
            def _insecure_httpx_client_factory(
                headers: Optional[Dict[str, str]] = None,
                timeout: Optional[httpx.Timeout] = None,
                auth: Optional[httpx.Auth] = None,
            ) -> httpx.AsyncClient:
                return httpx.AsyncClient(
                    headers=headers,
                    timeout=timeout,
                    auth=auth,
                    verify=False,
                )

            httpx_client_factory = _insecure_httpx_client_factory
            logger.warning("MCP SSE `%s`: SSL certificate verification disabled by config", alias)
        async with sse_client(
            url,
            headers=headers if headers else None,
            timeout=timeout,
            sse_read_timeout=sse_read,
            httpx_client_factory=httpx_client_factory,
        ) as rw:
            yield rw

    return _PersistentMcpServer(alias, "sse", _cm)


def _make_streamable_connector(alias: str, cfg: dict) -> _PersistentMcpServer:
    url = str(cfg.get("url") or "").strip()
    headers_raw = cfg.get("headers")
    headers: Optional[Dict[str, str]] = None
    if isinstance(headers_raw, dict):
        headers = {str(k): str(v) for k, v in headers_raw.items()}
    timeout_sec = float(cfg.get("timeout", 30))
    read_sec = float(cfg.get("sse_read_timeout", cfg.get("read_timeout", 300)))
    terminate = bool(cfg.get("terminate_on_close", True))

    @asynccontextmanager
    async def _cm() -> AsyncIterator[Tuple[Any, Any]]:
        to = httpx.Timeout(timeout_sec, read=read_sec)
        hc = create_mcp_http_client(headers=headers if headers else None, timeout=to)
        async with hc:
            async with streamable_http_client(url, http_client=hc, terminate_on_close=terminate) as streams:
                read, write, _ = streams
                yield (read, write)

    return _PersistentMcpServer(alias, "streamable-http", _cm)


async def _shutdown_servers_unlocked() -> None:
    global _fname_to_tool, _servers, _defs_snapshot
    for srv in list(_servers.values()):
        try:
            await srv.stop()
        except Exception:
            logger.exception("MCP stop failed for %s", getattr(srv, "alias", "?"))
    _servers.clear()
    _fname_to_tool.clear()
    _defs_snapshot.clear()


async def force_reload() -> None:
    """写入新配置后调用：关闭连接并于下次 ensure_started 重建。"""
    global _loaded_signature
    async with _start_lock:
        await _shutdown_servers_unlocked()
        _loaded_signature = None


async def ensure_started() -> None:
    global _loaded_signature
    if not _MCP_IMPORT_OK:
        return
    async with _start_lock:
        sig = _compute_config_signature()
        if sig == _loaded_signature:
            return

        await _shutdown_servers_unlocked()

        servers_cfg, err = _load_servers_dict_from_config()
        if not servers_cfg:
            _loaded_signature = sig
            if err:
                logger.info("MCP: skipped (%s)", err)
            elif _last_config_error:
                logger.info("MCP: skipped (%s)", _last_config_error)
            else:
                logger.info("MCP: no config (`mcp_servers.json` / MCP_SERVERS_JSON)")
            return

        for alias, cfg in servers_cfg.items():
            if not isinstance(cfg, dict):
                logger.warning("MCP: skip server `%s` (not an object)", alias)
                continue
            transport = _resolve_transport(cfg)
            srv: Optional[_PersistentMcpServer] = None
            try:
                if transport == "stdio":
                    if not str(cfg.get("command") or "").strip():
                        logger.warning("MCP: skip `%s` (stdio needs command)", alias)
                        continue
                    srv = _make_stdio_connector(alias, cfg)
                elif transport == "sse":
                    if not str(cfg.get("url") or "").strip():
                        logger.warning("MCP: skip `%s` (sse needs url)", alias)
                        continue
                    srv = _make_sse_connector(alias, cfg)
                elif transport == "streamable-http":
                    if not str(cfg.get("url") or "").strip():
                        logger.warning("MCP: skip `%s` (streamable-http needs url)", alias)
                        continue
                    srv = _make_streamable_connector(alias, cfg)
                else:
                    logger.warning("MCP: skip `%s` (unknown transport)", alias)
                    continue
                await srv.start()
            except Exception as e:
                logger.warning("MCP: failed to start `%s`: %s", alias, e)
                if srv:
                    await srv.stop()
                continue

            _servers[alias] = srv

            logger.info(
                "MCP: server `%s` OK transport=%s mapped_tools=%s",
                alias,
                getattr(srv, "transport_label", "?"),
                sum(1 for pair in _fname_to_tool.values() if pair[0] == alias),
            )

        _loaded_signature = sig


async def get_tool_definitions() -> List[Dict[str, Any]]:
    await ensure_started()
    return list(_defs_snapshot)


def format_call_tool_result(result: Any) -> str:
    if result is None:
        return ""
    err = getattr(result, "isError", False)
    parts: List[str] = []
    for block in getattr(result, "content", None) or []:
        btype = getattr(block, "type", None)
        if btype == "text":
            parts.append(str(getattr(block, "text", "") or ""))
        elif btype == "image":
            parts.append("[image content omitted]")
        elif btype == "resource":
            res = getattr(block, "resource", None)
            txt = getattr(res, "text", None) if res is not None else None
            if txt:
                parts.append(str(txt))
            else:
                parts.append(f"[resource: {getattr(res, 'uri', res)!s}]")
        else:
            parts.append(str(block))
    body = "\n".join(parts).strip()
    if err:
        prefix = "MCP tool returned an error."
        return f"{prefix}\n{body}" if body else prefix
    return body if body else repr(result)


async def invoke_tool_by_fname(function_name: str, arguments: Dict[str, Any]) -> str:
    await ensure_started()
    pair = _fname_to_tool.get(function_name)
    if not pair:
        return f"Error: unknown MCP tool `{function_name}`."
    alias, orig = pair
    srv = _servers.get(alias)
    if srv is None:
        return f"Error: MCP server `{alias}` is not running."
    if getattr(srv, "_task", None) is None or srv._task.done():
        try:
            await srv._restart()
        except Exception as e:
            return f"Error: MCP server `{alias}` reconnect failed: {e}"
    t0 = time.perf_counter()
    try:
        raw = await srv.call_tool(orig, arguments)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        raw_dump = _serialize_call_tool_result_for_log(raw)
        logger.info(
            "MCP call_ok server=%s transport=%s tool=%s fname=%s elapsed_ms=%.2f raw=%s",
            alias,
            getattr(srv, "transport_label", "?"),
            orig,
            function_name,
            elapsed_ms,
            raw_dump,
        )
        return format_call_tool_result(raw)
    except Exception as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        logger.warning(
            "MCP call_fail server=%s tool=%s fname=%s elapsed_ms=%.2f err=%s",
            alias,
            orig,
            function_name,
            elapsed_ms,
            e,
        )
        return f"MCP tool error ({function_name}): {e}"
