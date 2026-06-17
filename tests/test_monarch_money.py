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
    # It wires a managed MCP server factory + the page and gated-data routers.
    assert registry.mcp_servers, "expected a managed MCP server factory"
    prefixes = {prefix for prefix, _router in registry.routers}
    assert "/plugins/monarch-money" in prefixes  # public page
    assert "/api/plugins/monarch-money" in prefixes  # gated data
    assert registry.skill_dirs, "expected the bundled SKILL.md dir to be registered"


def test_mcp_factory_off_when_disabled(plugin):
    factory = plugin._server_factory
    assert factory(_cfg({"enabled": False, "token": "abc"})) is None


def test_mcp_factory_off_without_token(plugin):
    factory = plugin._server_factory
    assert factory(_cfg({"enabled": True, "token": ""})) is None
    assert factory(_cfg({"enabled": True})) is None


def test_mcp_factory_builds_entry_with_token(plugin):
    entry = plugin._server_factory(_cfg({"enabled": True, "token": "  secret-tok  "}))
    assert entry is not None
    assert entry["name"] == "monarch"
    assert entry["transport"] == "streamable_http"
    assert entry["url"] == "https://api.monarch.com/mcp"
    assert entry["headers"]["Authorization"] == "Bearer secret-tok"  # trimmed


def test_mcp_factory_respects_custom_url(plugin):
    entry = plugin._server_factory(_cfg({"enabled": True, "token": "t", "url": "https://staging.monarch.test/mcp"}))
    assert entry["url"] == "https://staging.monarch.test/mcp"


def test_status_payload_never_leaks_token(plugin):
    host = types.SimpleNamespace(config=lambda: _cfg({"enabled": True, "token": "supersecret"}))
    payload = plugin._status_payload(host)
    assert payload == {
        "enabled": True,
        "configured": True,
        "connected": True,
        "url": "https://api.monarch.com/mcp",
    }
    assert "supersecret" not in repr(payload)


def test_status_payload_host_free(plugin):
    # No host (or no config) → all False, default url, no crash.
    assert plugin._status_payload(None)["connected"] is False


def test_manifest_is_valid():
    m = yaml.safe_load((ROOT / "protoagent.plugin.yaml").read_text())
    assert m["id"] == "monarch-money" and m["version"]
    assert m["config_section"] == "monarch_money"
    assert "token" in m["secrets"]
    assert m["views"][0]["path"] == "/plugins/monarch-money/view"
