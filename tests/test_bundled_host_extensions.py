from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


from plugins.host import bundled_host_tool_definitions
from plugins.models import PluginDefinition
from plugins.security import PluginSecurityError


def test_external_manifest_cannot_self_grant_trusted_host(tmp_path):
    entry = tmp_path / "host.py"
    entry.write_text("def tool_definitions(context, plugin):\n    return []\n", encoding="utf-8")
    plugin = PluginDefinition(
        plugin_id="external.demo",
        name="External demo",
        namespace="external.demo",
        version="1.0.0",
        description="",
        author={},
        root=tmp_path,
        manifest_path=tmp_path / ".myagent-plugin" / "plugin.json",
        source_format="native",
        content_signature="test-signature",
        raw_manifest={
            "capabilities": {"trusted_host": {"entry": "host.py"}}
        },
    )

    with pytest.raises(PluginSecurityError, match="outside the bundled root"):
        bundled_host_tool_definitions([plugin])


def test_new_plugin_inside_bundled_directory_is_trusted_without_allowlist(
    tmp_path, monkeypatch
):
    import plugins.host as host

    root = tmp_path / "plugins" / "dynamic-host"
    root.mkdir(parents=True)
    entry = root / "host.py"
    entry.write_text(
        "def tool_definitions(context, plugin):\n"
        "    return [{'name': 'dynamic-tool'}]\n",
        encoding="utf-8",
    )
    plugin = PluginDefinition(
        plugin_id="dynamic-host",
        name="Dynamic host",
        namespace="dynamic-host",
        version="1.0.0",
        description="",
        author={},
        root=root,
        manifest_path=root / ".myagent-plugin" / "plugin.json",
        source_format="native",
        content_signature="test-signature",
        system_builtin=True,
        raw_manifest={"capabilities": {"trusted_host": {"entry": "host.py"}}},
    )
    monkeypatch.setattr(host, "_BUNDLED_ROOT", tmp_path / "plugins")

    assert host.bundled_host_tool_definitions([plugin]) == [
        {"name": "dynamic-tool"}
    ]


def test_bundled_plugin_directory_must_match_plugin_id(tmp_path, monkeypatch):
    import plugins.host as host

    root = tmp_path / "plugins" / "directory-name"
    root.mkdir(parents=True)
    entry = root / "host.py"
    entry.write_text("def tool_definitions(context, plugin):\n    return []\n", encoding="utf-8")
    plugin = PluginDefinition(
        plugin_id="manifest-name",
        name="Mismatched host",
        namespace="manifest-name",
        version="1.0.0",
        description="",
        author={},
        root=root,
        manifest_path=root / ".myagent-plugin" / "plugin.json",
        source_format="native",
        content_signature="mismatched-signature",
        raw_manifest={"capabilities": {"trusted_host": {"entry": "host.py"}}},
    )
    monkeypatch.setattr(host, "_BUNDLED_ROOT", tmp_path / "plugins")

    with pytest.raises(PluginSecurityError, match="not installed at"):
        host.bundled_host_tool_definitions([plugin])


def test_workflow_activation_skips_plugin_discovery_on_hot_path(monkeypatch):
    import agent_extensions
    import workflow_extensions

    calls = []

    class EmptyPlugins:
        plugins = ()

    def load_plugins():
        calls.append(True)
        return EmptyPlugins()

    workflow_extensions.invalidate_bundled_workflow_callbacks()
    monkeypatch.setattr(agent_extensions, "load_plugins", load_plugins)
    workflow_extensions.activate_bundled_workflow_callbacks(object())
    workflow_extensions.activate_bundled_workflow_callbacks(object())

    assert len(calls) == 1
    workflow_extensions.invalidate_bundled_workflow_callbacks()
