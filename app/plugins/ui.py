"""Validated, host-rendered UI contribution declarations for plugins."""
from __future__ import annotations

import re
import json
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping, Tuple

from .models import PluginDefinition
from .security import PluginSecurityError, PluginValidationError
from .settings import plugin_settings_schema
from .web import plugin_web_manifest, resolve_plugin_asset


_CONTRIBUTION_ID_RE = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
_MAX_NAVIGATION_ITEMS = 16
_MAX_MESSAGE_RENDERERS = 32
_MAX_SESSION_BADGES = 16
_MAX_SESSION_PANELS = 16
_MAX_PANEL_ACTIONS = 4
_MAX_ACTION_INPUTS = 8
_MAX_SETTINGS_SECTIONS = 16
_MAX_COMPOSER_ACTIONS = 16
_MAX_CHAT_EXTENSIONS = 16
_MAX_RENDERER_FIELDS = 12
_MAX_LIST_COLUMNS = 4
_MAX_LIST_ITEMS = 100
_MAX_LABEL_LENGTH = 64
_MAX_DESCRIPTION_LENGTH = 200
_MAX_POINTER_LENGTH = 160
_MIN_ORDER = -10_000
_MAX_ORDER = 10_000
_MESSAGE_VARIANTS = frozenset({"neutral", "info", "success", "warning", "danger"})
_FIELD_FORMATS = frozenset({"text", "number", "boolean", "json"})
_ACTION_INPUT_TYPES = frozenset({"string", "boolean", "integer", "number"})
_UNSAFE_POINTER_PARTS = frozenset({"__proto__", "prototype", "constructor"})
_BUNDLED_ROOT = Path(__file__).resolve().parents[2] / "plugins"


def _trusted_panel_renderer(plugin: PluginDefinition, raw: Any) -> Dict[str, str] | None:
    """Resolve a same-origin renderer for a physically bundled system plugin.

    A renderer executes in the host page and therefore has more authority than
    declarative UI metadata. A manifest flag alone must never grant that
    authority to a user-installed plugin.
    """

    if not isinstance(raw, Mapping):
        return None
    if not plugin.system_builtin or plugin.source_format != "native":
        return None
    try:
        plugin.root.resolve().relative_to(_BUNDLED_ROOT.resolve())
    except (OSError, RuntimeError, ValueError):
        return None
    result: Dict[str, str] = {}
    for key, suffixes in (("module", {".js", ".mjs"}), ("style", {".css"})):
        value = str(raw.get(key) or "").strip().replace("\\", "/")
        if not value:
            if key == "module":
                return None
            continue
        try:
            asset = resolve_plugin_asset(plugin, value)
        except (OSError, PluginSecurityError):
            return None
        if asset.suffix.lower() not in suffixes:
            return None
        signature = str(plugin.content_signature or "")[:64]
        result[key] = f"/plugin-assets/{plugin.plugin_id}/{value}?v={signature}"
    return result or None


def _action_inputs(raw: Any, fixed_arguments: Mapping[str, Any]) -> tuple[Dict[str, Any], ...] | None:
    if not isinstance(raw, list) or len(raw) > _MAX_ACTION_INPUTS:
        return None
    out: list[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            return None
        input_id = str(item.get("id") or "").strip()
        label = str(item.get("label") or input_id).strip()
        description = str(item.get("description") or "").strip()
        input_type = str(item.get("type") or "string").strip().lower()
        enum = item.get("enum")
        if (
            not _CONTRIBUTION_ID_RE.fullmatch(input_id)
            or input_id in seen
            or input_id in fixed_arguments
            or not label
            or len(label) > _MAX_LABEL_LENGTH
            or len(description) > _MAX_DESCRIPTION_LENGTH
            or input_type not in _ACTION_INPUT_TYPES
            or (
                enum is not None
                and (
                    not isinstance(enum, list)
                    or not 1 <= len(enum) <= 32
                    or any(not isinstance(value, (str, int, float, bool)) for value in enum)
                )
            )
        ):
            return None
        seen.add(input_id)
        definition: Dict[str, Any] = {
            "id": input_id,
            "label": label,
            "description": description,
            "type": input_type,
            "required": bool(item.get("required", False)),
        }
        if enum is not None:
            definition["enum"] = list(enum)
        if input_type == "string":
            for bound in ("min_length", "max_length"):
                if bound in item:
                    try:
                        definition[bound] = max(0, min(16_384, int(item.get(bound))))
                    except (TypeError, ValueError):
                        return None
            if definition.get("min_length", 0) > definition.get("max_length", 16_384):
                return None
        elif input_type in {"integer", "number"}:
            for bound in ("minimum", "maximum"):
                if bound in item:
                    value = item.get(bound)
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        return None
                    definition[bound] = value
            if definition.get("minimum", float("-inf")) > definition.get("maximum", float("inf")):
                return None
        out.append(definition)
    return tuple(out)


def _navigation_rows(raw: Any) -> list[Mapping[str, Any]]:
    if raw is True:
        return [{}]
    if not isinstance(raw, list):
        return []
    return [item for item in raw[:_MAX_NAVIGATION_ITEMS] if isinstance(item, Mapping)]


def _message_renderer_rows(raw_ui: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = raw_ui.get("message.renderer")
    if raw is None:
        raw = raw_ui.get("message_renderers")
    if not isinstance(raw, list):
        return []
    return [item for item in raw[:_MAX_MESSAGE_RENDERERS] if isinstance(item, Mapping)]


def _slot_rows(
    raw_ui: Mapping[str, Any],
    slot: str,
    alias: str,
    limit: int,
) -> list[Mapping[str, Any]]:
    raw = raw_ui.get(slot)
    if raw is None:
        raw = raw_ui.get(alias)
    if not isinstance(raw, list):
        return []
    return [item for item in raw[:limit] if isinstance(item, Mapping)]


def _json_pointer(raw: Any) -> str:
    value = str(raw or "").strip()
    if not value or len(value) > _MAX_POINTER_LENGTH or not value.startswith("/"):
        return ""
    parts = value[1:].split("/")
    decoded = [part.replace("~1", "/").replace("~0", "~") for part in parts]
    if any(not part or part in _UNSAFE_POINTER_PARTS for part in decoded):
        return ""
    return value


def _renderer_fields(raw: Any, *, allow_list: bool = False) -> tuple[Dict[str, Any], ...]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= _MAX_RENDERER_FIELDS:
        return ()
    out: list[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            return ()
        pointer = _json_pointer(item.get("path"))
        label = str(item.get("label") or "").strip()
        value_format = str(item.get("format") or "text").strip().lower()
        if value_format == "list" and allow_list:
            if (
                not pointer
                or pointer in seen
                or not label
                or len(label) > _MAX_LABEL_LENGTH
            ):
                return ()
            raw_columns = item.get("columns")
            if not isinstance(raw_columns, list) or not 1 <= len(raw_columns) <= _MAX_LIST_COLUMNS:
                return ()
            columns = []
            column_paths = set()
            for raw_column in raw_columns:
                if not isinstance(raw_column, Mapping):
                    return ()
                column_path = _json_pointer(raw_column.get("path"))
                column_label = str(raw_column.get("label") or "").strip()
                column_format = str(raw_column.get("format") or "text").strip().lower()
                if (
                    not column_path
                    or column_path in column_paths
                    or not column_label
                    or len(column_label) > _MAX_LABEL_LENGTH
                    or column_format not in _FIELD_FORMATS
                ):
                    return ()
                column_paths.add(column_path)
                columns.append(
                    {"path": column_path, "label": column_label, "format": column_format}
                )
            seen.add(pointer)
            out.append(
                {
                    "path": pointer,
                    "label": label,
                    "format": "list",
                    "optional": bool(item.get("optional", True)),
                    "columns": columns,
                }
            )
            continue
        if (
            not pointer
            or pointer in seen
            or not label
            or len(label) > _MAX_LABEL_LENGTH
            or value_format not in _FIELD_FORMATS
        ):
            return ()
        seen.add(pointer)
        out.append(
            {
                "path": pointer,
                "label": label,
                "format": value_format,
                "optional": bool(item.get("optional", True)),
            }
        )
    return tuple(out)


def plugin_ui_contributions(plugin: PluginDefinition) -> Tuple[Dict[str, Any], ...]:
    """Return safe host-owned UI slots declared by one plugin.

    Ordinary plugins supply metadata only. The host constructs destination
    URLs and renders labels as text, so a manifest cannot inject markup or
    redirect a navigation item to an arbitrary origin. Physically bundled
    system plugins may additionally opt into the explicitly trusted panel
    renderer path validated by :func:`_trusted_panel_renderer`.
    """

    capabilities = plugin.raw_manifest.get("capabilities")
    raw_ui = capabilities.get("ui") if isinstance(capabilities, Mapping) else None
    if not isinstance(raw_ui, Mapping):
        raw_ui = {}
    try:
        settings_schema = plugin_settings_schema(plugin)
    except PluginValidationError:
        settings_schema = None
    if not raw_ui and settings_schema is None:
        return ()

    seen: set[tuple[str, str]] = set()
    out: list[Dict[str, Any]] = []
    try:
        web = plugin_web_manifest(plugin)
    except (OSError, PluginSecurityError):
        web = None
    if web is not None and web.entry is not None:
        for raw in _navigation_rows(raw_ui.get("navigation")):
            contribution_id = str(raw.get("id") or "main").strip()
            raw_label = plugin.name if "label" not in raw else raw.get("label")
            label = str(raw_label or "").strip()
            description = str(raw.get("description") or "").strip()
            key = ("navigation", contribution_id)
            if (
                not _CONTRIBUTION_ID_RE.fullmatch(contribution_id)
                or key in seen
                or not label
                or len(label) > _MAX_LABEL_LENGTH
                or len(description) > _MAX_DESCRIPTION_LENGTH
            ):
                continue
            seen.add(key)
            try:
                order = int(raw.get("order", 100))
            except (TypeError, ValueError):
                order = 100
            order = max(_MIN_ORDER, min(_MAX_ORDER, order))
            out.append(
                {
                    "id": contribution_id,
                    "plugin_id": plugin.plugin_id,
                    "slot": "navigation",
                    "label": label,
                    "description": description,
                    "order": order,
                    "href": f"/plugins/{plugin.plugin_id}",
                    "target": "plugin-page",
                }
            )

    settings_section_declared = (
        "settings.section" in raw_ui or "settings_sections" in raw_ui
    )
    settings_rows = _slot_rows(
        raw_ui, "settings.section", "settings_sections", _MAX_SETTINGS_SECTIONS
    )
    if settings_schema is not None and not settings_rows and not settings_section_declared:
        settings_rows = [{}]
    for raw in settings_rows:
        contribution_id = str(raw.get("id") or "main").strip()
        title = str(raw.get("title") or (settings_schema or {}).get("title") or plugin.name).strip()
        target = str(raw.get("target") or ("settings" if settings_schema else "plugin-page")).strip()
        label = str(raw.get("label") or ("Save" if target == "settings" else "Open")).strip()
        description = str(
            raw.get("description")
            or ((settings_schema or {}).get("description") if target == "settings" else "")
            or ""
        ).strip()
        key = ("settings.section", contribution_id)
        if (
            not _CONTRIBUTION_ID_RE.fullmatch(contribution_id)
            or key in seen
            or not title
            or not label
            or len(title) > _MAX_LABEL_LENGTH
            or len(label) > _MAX_LABEL_LENGTH
            or len(description) > _MAX_DESCRIPTION_LENGTH
            or target not in {"settings", "plugin-page"}
            or (target == "settings" and settings_schema is None)
            or (target == "plugin-page" and (web is None or web.entry is None))
        ):
            continue
        seen.add(key)
        try:
            order = int(raw.get("order", 100))
        except (TypeError, ValueError):
            order = 100
        contribution = {
            "id": contribution_id,
            "plugin_id": plugin.plugin_id,
            "slot": "settings.section",
            "title": title,
            "label": label,
            "description": description,
            "order": max(_MIN_ORDER, min(_MAX_ORDER, order)),
            "target": "plugin-settings" if target == "settings" else "plugin-page",
        }
        if target == "settings":
            contribution["endpoint"] = f"/api/plugins/{plugin.plugin_id}/settings"
        else:
            contribution["href"] = f"/plugins/{plugin.plugin_id}"
        out.append(contribution)

    for raw in _slot_rows(
        raw_ui, "composer.action", "composer_actions", _MAX_COMPOSER_ACTIONS
    ):
        contribution_id = str(raw.get("id") or "").strip()
        label = str(raw.get("label") or "").strip()
        description = str(raw.get("description") or "").strip()
        action = str(raw.get("action") or "insert_text").strip().lower()
        text = str(raw.get("text") or "")
        key = ("composer.action", contribution_id)
        if (
            not _CONTRIBUTION_ID_RE.fullmatch(contribution_id)
            or key in seen
            or not label
            or len(label) > _MAX_LABEL_LENGTH
            or len(description) > _MAX_DESCRIPTION_LENGTH
            or action not in {"insert_text", "open_plugin_page"}
            or (action == "insert_text" and (not text.strip() or len(text) > 2000))
            or (action == "open_plugin_page" and (web is None or web.entry is None))
        ):
            continue
        seen.add(key)
        try:
            order = int(raw.get("order", 100))
        except (TypeError, ValueError):
            order = 100
        contribution = {
            "id": contribution_id,
            "plugin_id": plugin.plugin_id,
            "slot": "composer.action",
            "label": label,
            "description": description,
            "order": max(_MIN_ORDER, min(_MAX_ORDER, order)),
            "action": action,
        }
        if action == "insert_text":
            contribution["text"] = text
        else:
            contribution["href"] = f"/plugins/{plugin.plugin_id}"
        out.append(contribution)

    for raw in _message_renderer_rows(raw_ui):
        contribution_id = str(raw.get("id") or "").strip()
        event_name = str(raw.get("event_name") or raw.get("event") or "").strip()
        title = str(raw.get("title") or plugin.name).strip()
        description = str(raw.get("description") or "").strip()
        variant = str(raw.get("variant") or "neutral").strip().lower()
        fields = _renderer_fields(raw.get("fields"))
        key = ("message.renderer", contribution_id)
        event_key = ("message.event", event_name)
        if (
            not _CONTRIBUTION_ID_RE.fullmatch(contribution_id)
            or not _CONTRIBUTION_ID_RE.fullmatch(event_name)
            or key in seen
            or event_key in seen
            or not title
            or len(title) > _MAX_LABEL_LENGTH
            or len(description) > _MAX_DESCRIPTION_LENGTH
            or variant not in _MESSAGE_VARIANTS
            or not fields
        ):
            continue
        seen.add(key)
        seen.add(event_key)
        out.append(
            {
                "id": contribution_id,
                "plugin_id": plugin.plugin_id,
                "slot": "message.renderer",
                "event_name": event_name,
                "title": title,
                "description": description,
                "variant": variant,
                "fields": list(fields),
            }
        )

    for raw in _slot_rows(raw_ui, "session.badge", "session_badges", _MAX_SESSION_BADGES):
        contribution_id = str(raw.get("id") or "").strip()
        namespace = str(raw.get("namespace") or "default").strip()
        pointer = _json_pointer(raw.get("path"))
        label = str(raw.get("label") or "").strip()
        description = str(raw.get("description") or "").strip()
        variant = str(raw.get("variant") or "neutral").strip().lower()
        operator = str(raw.get("when") or "truthy").strip().lower()
        display = str(raw.get("display") or "badge").strip().lower()
        key = ("session.badge", contribution_id)
        equals = raw.get("equals")
        equals_is_scalar = equals is None or isinstance(equals, (str, int, float, bool))
        if (
            not _CONTRIBUTION_ID_RE.fullmatch(contribution_id)
            or not _CONTRIBUTION_ID_RE.fullmatch(namespace)
            or not pointer
            or key in seen
            or not label
            or len(label) > _MAX_LABEL_LENGTH
            or len(description) > _MAX_DESCRIPTION_LENGTH
            or variant not in _MESSAGE_VARIANTS
            or operator not in {"truthy", "equals", "not_equals"}
            or display not in {"badge", "activity"}
            or not equals_is_scalar
        ):
            continue
        seen.add(key)
        contribution = {
            "id": contribution_id,
            "plugin_id": plugin.plugin_id,
            "slot": "session.badge",
            "namespace": namespace,
            "path": pointer,
            "label": label,
            "description": description,
            "variant": variant,
            "when": operator,
        }
        if operator in {"equals", "not_equals"}:
            contribution["equals"] = equals
        if display != "badge":
            contribution["display"] = display
        out.append(contribution)

    for raw in _slot_rows(raw_ui, "session.panel", "session_panels", _MAX_SESSION_PANELS):
        contribution_id = str(raw.get("id") or "").strip()
        namespace = str(raw.get("namespace") or "default").strip()
        title = str(raw.get("title") or plugin.name).strip()
        description = str(raw.get("description") or "").strip()
        variant = str(raw.get("variant") or "neutral").strip().lower()
        fields = _renderer_fields(raw.get("fields"), allow_list=True)
        raw_visible_when = raw.get("visible_when")
        visible_when = None
        if raw_visible_when is not None:
            if not isinstance(raw_visible_when, Mapping):
                continue
            visible_path = _json_pointer(raw_visible_when.get("path"))
            visible_operator = str(raw_visible_when.get("when") or "truthy").strip().lower()
            visible_equals = raw_visible_when.get("equals")
            if (
                not visible_path
                or visible_operator not in {"truthy", "not_truthy", "equals", "not_equals"}
                or not (
                    visible_equals is None
                    or isinstance(visible_equals, (str, int, float, bool))
                )
            ):
                continue
            visible_when = {"path": visible_path, "when": visible_operator}
            if visible_operator in {"equals", "not_equals"}:
                visible_when["equals"] = visible_equals
        actions = []
        raw_actions = raw.get("actions")
        if raw_actions is not None:
            if not isinstance(raw_actions, list) or len(raw_actions) > _MAX_PANEL_ACTIONS:
                continue
            for raw_action in raw_actions:
                if not isinstance(raw_action, Mapping):
                    actions = []
                    break
                action_id = str(raw_action.get("id") or "").strip()
                action_label = str(raw_action.get("label") or "").strip()
                action_variant = str(raw_action.get("variant") or "neutral").strip().lower()
                operation = str(raw_action.get("operation") or "").strip().lower()
                confirm = str(raw_action.get("confirm") or "").strip()
                tool_name = str(raw_action.get("tool") or "").strip()
                fixed_arguments = raw_action.get("arguments", {})
                try:
                    encoded_value = json.dumps(
                        raw_action.get("value") if operation == "set_state" else fixed_arguments,
                        ensure_ascii=False,
                        allow_nan=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                except (TypeError, ValueError):
                    actions = []
                    break
                if (
                    not _CONTRIBUTION_ID_RE.fullmatch(action_id)
                    or not action_label
                    or len(action_label) > _MAX_LABEL_LENGTH
                    or action_variant not in _MESSAGE_VARIANTS
                    or operation not in {"set_state", "invoke_tool"}
                    or (
                        operation == "invoke_tool"
                        and (
                            not _CONTRIBUTION_ID_RE.fullmatch(tool_name)
                            or not isinstance(fixed_arguments, Mapping)
                        )
                    )
                    or len(confirm) > _MAX_DESCRIPTION_LENGTH
                    or len(encoded_value) > 16 * 1024
                ):
                    actions = []
                    break
                action_definition = {
                    "id": action_id,
                    "label": action_label,
                    "variant": action_variant,
                    "operation": operation,
                    "confirm": confirm,
                }
                if operation == "set_state":
                    action_definition["state_value"] = raw_action.get("value")
                else:
                    inputs = _action_inputs(raw_action.get("inputs", []), fixed_arguments)
                    if inputs is None:
                        actions = []
                        break
                    action_definition.update(
                        {
                            "tool": tool_name,
                            "arguments": dict(fixed_arguments),
                            "inputs": list(inputs),
                        }
                    )
                actions.append(action_definition)
            if len(actions) != len(raw_actions):
                continue
        key = ("session.panel", contribution_id)
        if (
            not _CONTRIBUTION_ID_RE.fullmatch(contribution_id)
            or not _CONTRIBUTION_ID_RE.fullmatch(namespace)
            or key in seen
            or not title
            or len(title) > _MAX_LABEL_LENGTH
            or len(description) > _MAX_DESCRIPTION_LENGTH
            or variant not in _MESSAGE_VARIANTS
            or not fields
        ):
            continue
        seen.add(key)
        contribution = {
            "id": contribution_id,
            "plugin_id": plugin.plugin_id,
            "slot": "session.panel",
            "namespace": namespace,
            "title": title,
            "description": description,
            "variant": variant,
            "fields": list(fields),
        }
        if "order" in raw:
            try:
                order = int(raw.get("order", 100))
            except (TypeError, ValueError):
                order = 100
            contribution["order"] = max(_MIN_ORDER, min(_MAX_ORDER, order))
        if visible_when is not None:
            contribution["visible_when"] = visible_when
        renderer = _trusted_panel_renderer(plugin, raw.get("renderer"))
        if renderer is not None:
            contribution["renderer"] = renderer
        if actions:
            contribution["actions"] = actions
        out.append(contribution)

    for raw in _slot_rows(raw_ui, "chat.extension", "chat_extensions", _MAX_CHAT_EXTENSIONS):
        contribution_id = str(raw.get("id") or "").strip()
        key = ("chat.extension", contribution_id)
        renderer = _trusted_panel_renderer(plugin, raw.get("renderer"))
        if (
            not _CONTRIBUTION_ID_RE.fullmatch(contribution_id)
            or key in seen
            or renderer is None
        ):
            continue
        seen.add(key)
        out.append(
            {
                "id": contribution_id,
                "plugin_id": plugin.plugin_id,
                "slot": "chat.extension",
                "renderer": renderer,
            }
        )
    return tuple(out)


def _pointer_value(value: Any, pointer: str) -> tuple[bool, Any]:
    current = value
    for encoded in pointer[1:].split("/"):
        part = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return False, None
        else:
            return False, None
    return True, current


def _projected_text(value: Any, value_format: str) -> str:
    if value_format == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return ""
        return str(value)
    if value_format == "boolean":
        if not isinstance(value, bool):
            return ""
        return "true" if value else "false"
    if value_format == "json" or isinstance(value, (Mapping, list, tuple)):
        try:
            text = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError):
            return ""
        return text[:4000] + ("…" if len(text) > 4000 else "")
    text = str(value if value is not None else "").replace("\r", " ").replace("\n", " ").strip()
    return text[:1000] + ("…" if len(text) > 1000 else "")


def project_plugin_session_ui(
    plugins: Iterable[PluginDefinition],
    session_ids: Iterable[str],
    snapshot_reader: Callable[[str], Mapping[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Project only manifest-whitelisted extension state into browser view models."""

    declarations = [
        row
        for plugin in plugins
        for row in plugin_ui_contributions(plugin)
        if row.get("slot") in {"session.badge", "session.panel"}
    ]
    declarations.sort(key=lambda row: int(row.get("order", 100)))
    out: Dict[str, Dict[str, Any]] = {}
    for raw_session_id in list(session_ids)[:200]:
        session_id = str(raw_session_id or "").strip()
        if not session_id or session_id in out:
            continue
        result: Dict[str, Any] = {"badges": [], "panels": []}
        try:
            snapshot = snapshot_reader(session_id)
        except Exception:
            snapshot = {}
        extensions = snapshot.get("extensions") if isinstance(snapshot, Mapping) else {}
        for declaration in declarations:
            plugin_id = str(declaration["plugin_id"])
            namespace = str(declaration["namespace"])
            plugin_state = extensions.get(plugin_id) if isinstance(extensions, Mapping) else None
            row = plugin_state.get(namespace) if isinstance(plugin_state, Mapping) else None
            value = row.get("value") if isinstance(row, Mapping) else None
            if not isinstance(row, Mapping) or value is None:
                continue
            if declaration["slot"] == "session.badge":
                found, selected = _pointer_value(value, str(declaration["path"]))
                operator = str(declaration.get("when") or "truthy")
                visible = bool(selected) if found else False
                if operator == "equals":
                    visible = found and selected == declaration.get("equals")
                elif operator == "not_equals":
                    visible = found and selected != declaration.get("equals")
                if visible:
                    badge = {
                            "plugin_id": plugin_id,
                            "id": declaration["id"],
                            "label": declaration["label"],
                            "description": declaration["description"],
                            "variant": declaration["variant"],
                            "revision": int(row.get("revision") or 0),
                    }
                    if declaration.get("display"):
                        badge["display"] = declaration["display"]
                    result["badges"].append(badge)
                continue
            condition = declaration.get("visible_when")
            if isinstance(condition, Mapping):
                found, selected = _pointer_value(value, str(condition["path"]))
                operator = str(condition.get("when") or "truthy")
                visible = bool(selected) if found else False
                if operator == "not_truthy":
                    visible = not found or not bool(selected)
                elif operator == "equals":
                    visible = found and selected == condition.get("equals")
                elif operator == "not_equals":
                    visible = found and selected != condition.get("equals")
                if not visible:
                    continue
            fields = []
            for field in declaration["fields"]:
                found, selected = _pointer_value(value, str(field["path"]))
                if field["format"] == "list":
                    rows = []
                    selected_rows = (
                        list(selected.values())
                        if found and isinstance(selected, Mapping)
                        else selected
                        if found and isinstance(selected, list)
                        else []
                    )
                    if selected_rows:
                        for selected_row in selected_rows[:_MAX_LIST_ITEMS]:
                            values = []
                            for column in field["columns"]:
                                column_found, column_value = _pointer_value(
                                    selected_row, str(column["path"])
                                )
                                values.append(
                                    _projected_text(column_value, str(column["format"]))
                                    if column_found
                                    else ""
                                )
                            rows.append({"values": values})
                    if not rows and field.get("optional"):
                        continue
                    fields.append(
                        {
                            "label": field["label"],
                            "format": "list",
                            "columns": [
                                {"label": column["label"], "format": column["format"]}
                                for column in field["columns"]
                            ],
                            "rows": rows,
                        }
                    )
                    continue
                formatted = _projected_text(selected, str(field["format"])) if found else ""
                if not formatted and field.get("optional"):
                    continue
                fields.append(
                    {
                        "label": field["label"],
                        "format": field["format"],
                        "value": formatted or "—",
                    }
                )
            panel = {
                "plugin_id": plugin_id,
                "id": declaration["id"],
                "title": declaration["title"],
                "description": declaration["description"],
                "variant": declaration["variant"],
                "revision": int(row.get("revision") or 0),
                "fields": fields,
            }
            if declaration.get("actions"):
                projected_actions = []
                for action in declaration["actions"]:
                    projected_action = {
                        "id": action["id"],
                        "label": action["label"],
                        "variant": action["variant"],
                        "confirm": action["confirm"],
                    }
                    if action.get("inputs"):
                        projected_action["inputs"] = action["inputs"]
                    projected_actions.append(projected_action)
                panel["actions"] = projected_actions
            result["panels"].append(panel)
        out[session_id] = result
    return out


def plugin_session_action_definition(
    plugins: Iterable[PluginDefinition],
    plugin_id: str,
    action_id: str,
) -> Dict[str, Any] | None:
    owner = str(plugin_id or "").strip()
    requested = str(action_id or "").strip()
    for plugin in plugins:
        if plugin.plugin_id != owner:
            continue
        for contribution in plugin_ui_contributions(plugin):
            if contribution.get("slot") != "session.panel":
                continue
            for action in contribution.get("actions") or []:
                if action.get("id") == requested:
                    definition = {
                        "plugin_id": owner,
                        "namespace": contribution["namespace"],
                        "action_id": requested,
                        "operation": action["operation"],
                        "state_value": action.get("state_value"),
                    }
                    if action["operation"] == "invoke_tool":
                        definition.update(
                            {
                                "tool": action.get("tool"),
                                "arguments": action.get("arguments", {}),
                                "inputs": action.get("inputs", []),
                            }
                        )
                    return definition
    return None


def plugin_session_action_arguments(
    definition: Mapping[str, Any],
    supplied: Any,
) -> Dict[str, Any]:
    """Validate browser values against one manifest-declared action schema."""

    if definition.get("operation") != "invoke_tool":
        return {}
    if supplied is None:
        supplied = {}
    if not isinstance(supplied, Mapping):
        raise PluginValidationError("session action inputs must be an object")
    inputs = definition.get("inputs") or []
    allowed = {str(item.get("id") or "") for item in inputs if isinstance(item, Mapping)}
    unknown = set(map(str, supplied.keys())) - allowed
    if unknown:
        raise PluginValidationError(f"unknown session action input: {sorted(unknown)[0]}")
    arguments = dict(definition.get("arguments") or {})
    for item in inputs:
        input_id = str(item["id"])
        if input_id not in supplied:
            if item.get("required"):
                raise PluginValidationError(f"missing session action input: {input_id}")
            continue
        value = supplied.get(input_id)
        input_type = str(item.get("type") or "string")
        if input_type == "string":
            if not isinstance(value, str):
                raise PluginValidationError(f"{input_id} must be a string")
            if len(value) < int(item.get("min_length", 0)) or len(value) > int(
                item.get("max_length", 16_384)
            ):
                raise PluginValidationError(f"{input_id} has an invalid length")
        elif input_type == "boolean":
            if not isinstance(value, bool):
                raise PluginValidationError(f"{input_id} must be a boolean")
        elif input_type == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise PluginValidationError(f"{input_id} must be an integer")
        elif input_type == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise PluginValidationError(f"{input_id} must be a number")
        if input_type in {"integer", "number"}:
            if "minimum" in item and value < item["minimum"]:
                raise PluginValidationError(f"{input_id} is below its minimum")
            if "maximum" in item and value > item["maximum"]:
                raise PluginValidationError(f"{input_id} is above its maximum")
        if "enum" in item and value not in item["enum"]:
            raise PluginValidationError(f"{input_id} is not an allowed value")
        arguments[input_id] = value
    encoded = json.dumps(arguments, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(encoded) > 32 * 1024:
        raise PluginValidationError("session action arguments exceed 32768 bytes")
    return arguments


__all__ = [
    "plugin_session_action_arguments",
    "plugin_session_action_definition",
    "plugin_ui_contributions",
    "project_plugin_session_ui",
]
