"""Trusted in-process adapters for bundled workflow plugins.

Ordinary plugins always run out of process. A plugin physically shipped under
the repository's bundled ``plugins`` directory may additionally declare a
small host adapter for FastAPI routers and lifecycle callbacks that need core
service facades. The location check is host policy; a manifest alone cannot
grant this trust level.
"""
from __future__ import annotations

import hashlib
import importlib.util
import inspect
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping, Sequence

from .models import PluginDefinition
from .security import PluginSecurityError


_BUNDLED_ROOT = Path(__file__).resolve().parents[2] / "plugins"
_MODULES: dict[tuple[str, str], ModuleType] = {}
_INSTALLED: set[tuple[int, str, str]] = set()
_STARTED: list[tuple[PluginDefinition, ModuleType]] = []


def is_bundled_trusted_host_plugin(plugin: PluginDefinition) -> bool:
    """Return whether a native plugin is installed directly under ``plugins/``.

    The bundled directory is the host-controlled trust boundary.  Requiring the
    resolved root to be exactly ``plugins/<plugin id>`` prevents manifests from
    granting in-process trust to plugins discovered from user or nested paths,
    while allowing newly bundled plugin directories without a second allowlist.
    """

    if plugin.source_format != "native":
        return False
    try:
        bundled_root = _BUNDLED_ROOT.resolve(strict=True)
        root = plugin.root.resolve(strict=True)
        expected_root = (_BUNDLED_ROOT / plugin.plugin_id).resolve(strict=True)
        root.relative_to(bundled_root)
        return root.is_dir() and root == expected_root
    except (OSError, RuntimeError, ValueError):
        return False


def _host_entry(plugin: PluginDefinition) -> Path | None:
    capabilities = plugin.raw_manifest.get("capabilities")
    raw = capabilities.get("trusted_host") if isinstance(capabilities, Mapping) else None
    if not isinstance(raw, Mapping):
        return None
    entry = str(raw.get("entry") or "").strip()
    if not entry:
        return None
    root = plugin.root.resolve()
    try:
        root.relative_to(_BUNDLED_ROOT.resolve())
    except ValueError as exc:
        raise PluginSecurityError(
            f"Plugin {plugin.plugin_id!r} requested trusted_host outside the bundled root"
        ) from exc
    if not is_bundled_trusted_host_plugin(plugin):
        raise PluginSecurityError(
            f"Plugin {plugin.plugin_id!r} is not installed at "
            f"plugins/{plugin.plugin_id} under the bundled root"
        )
    candidate = (root / entry).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise PluginSecurityError("trusted_host entry escapes the plugin root") from exc
    if candidate.suffix.lower() != ".py" or not candidate.is_file():
        raise PluginSecurityError("trusted_host entry must be an existing Python file")
    return candidate


def _module(plugin: PluginDefinition) -> ModuleType | None:
    entry = _host_entry(plugin)
    if entry is None:
        return None
    key = (plugin.plugin_id, plugin.content_signature)
    cached = _MODULES.get(key)
    if cached is not None:
        return cached
    digest = hashlib.sha256(f"{entry}:{plugin.content_signature}".encode()).hexdigest()[:16]
    name = f"myagent_bundled_host_{digest}"
    spec = importlib.util.spec_from_file_location(name, entry)
    if spec is None or spec.loader is None:
        raise PluginSecurityError(f"Cannot load trusted host entry: {entry}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    _MODULES[key] = module
    return module


def bundled_host_plugin_enabled(plugin_id: str) -> bool:
    """Return live enablement for a bundled host extension."""

    try:
        from .manager import get_plugin_manager, plugins_enabled

        manager = get_plugin_manager()
        return bool(plugins_enabled() and manager.is_enabled(str(plugin_id or "")))
    except Exception:
        return False


def bundled_host_tool_definitions(
    plugins: Sequence[PluginDefinition],
    *,
    session_meta: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Activate and describe tools owned by enabled bundled extensions.

    The callable is accepted only from a physically bundled, host-validated
    module. Ordinary manifests cannot register in-process invokers.
    """

    rows: list[dict[str, Any]] = []
    context = {
        "is_enabled": bundled_host_plugin_enabled,
        "session_meta": dict(session_meta or {}),
    }
    for plugin in plugins:
        module = _module(plugin)
        describe = getattr(module, "tool_definitions", None) if module is not None else None
        if not callable(describe):
            continue
        definitions = describe(dict(context), plugin)
        if not isinstance(definitions, (list, tuple)):
            raise PluginSecurityError("trusted host tool_definitions must return a list")
        for definition in definitions:
            if not isinstance(definition, Mapping):
                raise PluginSecurityError("trusted host tool definition must be an object")
            rows.append(dict(definition))
    return rows


def activate_bundled_provider_extensions(
    plugins: Sequence[PluginDefinition],
    registry: Any,
    *,
    registration_method: str = "register_providers",
) -> None:
    """Let only physically bundled host adapters register hot-path providers."""

    method = str(registration_method or "").strip()
    if method not in {"register_providers", "register_search_providers"}:
        raise PluginSecurityError("unsupported bundled provider registration method")
    for plugin in plugins:
        module = _module(plugin)
        register = getattr(module, method, None) if module is not None else None
        if callable(register):
            register(registry, plugin)


def install_bundled_host_extensions(
    app: Any,
    plugins: Sequence[PluginDefinition],
    context: Mapping[str, Any],
) -> None:
    """Install enabled bundled routers before the ASGI app starts."""

    for plugin in plugins:
        module = _module(plugin)
        install = getattr(module, "install", None) if module is not None else None
        key = (id(app), plugin.plugin_id, plugin.content_signature)
        if callable(install) and key not in _INSTALLED:
            install(app, dict(context), plugin)
            _INSTALLED.add(key)


async def start_bundled_host_extensions(
    plugins: Sequence[PluginDefinition],
    context: Mapping[str, Any],
) -> None:
    global _STARTED
    await stop_bundled_host_extensions(context)
    started: list[tuple[PluginDefinition, ModuleType]] = []
    for plugin in plugins:
        module = _module(plugin)
        start = getattr(module, "start", None) if module is not None else None
        if callable(start):
            value = start(dict(context), plugin)
            if inspect.isawaitable(value):
                await value
            started.append((plugin, module))
    _STARTED = started


async def stop_bundled_host_extensions(context: Mapping[str, Any]) -> None:
    global _STARTED
    started, _STARTED = list(reversed(_STARTED)), []
    for plugin, module in started:
        stop = getattr(module, "stop", None)
        if callable(stop):
            value = stop(dict(context), plugin)
            if inspect.isawaitable(value):
                await value


__all__ = [
    "is_bundled_trusted_host_plugin",
    "install_bundled_host_extensions",
    "bundled_host_plugin_enabled",
    "bundled_host_tool_definitions",
    "activate_bundled_provider_extensions",
    "start_bundled_host_extensions",
    "stop_bundled_host_extensions",
]
