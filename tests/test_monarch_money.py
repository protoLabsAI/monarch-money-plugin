"""Smoke tests for Monarch Money — host-free (no protoAgent running)."""

from __future__ import annotations

import types
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _cfg(section: dict):
    """A stand-in LangGraphConfig carrying just this plugin's config section."""
    return types.SimpleNamespace(plugin_config={"monarch_money": section})


def test_register_runs_host_free(plugin, registry):
    plugin.register(registry)  # must not raise with no host present
    # It wires a managed MCP server factory + the page and gated-data routers + the skill.
    assert registry.mcp_servers, "expected a managed MCP server factory"
    prefixes = {prefix for prefix, _router in registry.routers}
    assert "/plugins/monarch-money" in prefixes  # public page
    assert "/api/plugins/monarch-money" in prefixes  # gated data
    assert registry.skill_dirs, "expected the bundled SKILL.md dir to be registered"


def test_mcp_factory_off_when_disabled(plugin, monkeypatch):
    monkeypatch.setattr(plugin.shutil, "which", lambda _: "/usr/bin/npx")
    assert plugin._server_factory(_cfg({"enabled": False})) is None


def test_mcp_factory_off_without_npx(plugin, monkeypatch):
    monkeypatch.setattr(plugin.shutil, "which", lambda _: None)  # no Node on PATH
    assert plugin._server_factory(_cfg({"enabled": True})) is None


def test_mcp_factory_builds_stdio_bridge(plugin, monkeypatch):
    monkeypatch.setattr(plugin.shutil, "which", lambda _: "/usr/bin/npx")
    entry = plugin._server_factory(_cfg({"enabled": True}))
    assert entry is not None
    assert entry["name"] == "monarch"
    assert entry["transport"] == "stdio"
    assert entry["command"] == "/usr/bin/npx"
    assert entry["args"] == ["-y", "mcp-remote@0.1.38", "https://api.monarch.com/mcp"]  # pinned default


def test_mcp_factory_respects_custom_url_and_package(plugin, monkeypatch):
    monkeypatch.setattr(plugin.shutil, "which", lambda _: "/usr/bin/npx")
    entry = plugin._server_factory(
        _cfg({"enabled": True, "url": "https://staging.monarch.test/mcp", "package": "mcp-remote@0.1.18"})
    )
    assert entry["args"] == ["-y", "mcp-remote@0.1.18", "https://staging.monarch.test/mcp"]


def test_status_payload_shape(plugin, monkeypatch):
    monkeypatch.setattr(plugin.shutil, "which", lambda _: "/usr/bin/npx")
    monkeypatch.setattr(plugin, "_has_oauth_cache", lambda: False)
    host = types.SimpleNamespace(config=lambda: _cfg({"enabled": True}))
    payload = plugin._status_payload(host)
    assert payload == {
        "enabled": True,
        "npx_available": True,
        "authorized": False,
        "connected": True,
        "url": "https://api.monarch.com/mcp",
    }


def test_status_payload_host_free(plugin):
    # No host (or no config) → not enabled, not connected, no crash.
    s = plugin._status_payload(None)
    assert s["enabled"] is False and s["connected"] is False


def test_manifest_is_valid():
    m = yaml.safe_load((ROOT / "protoagent.plugin.yaml").read_text())
    assert m["id"] == "monarch-money" and m["version"]
    assert m["config_section"] == "monarch_money"
    # OAuth bridge — no pasted secret in the manifest.
    assert not m.get("secrets"), "OAuth design should not declare a static secret"
    keys = {s["key"] for s in m["settings"]}
    assert {"enabled", "url", "package"} <= keys
    assert m["views"][0]["path"] == "/plugins/monarch-money/view"
