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
    # The pre-auth gate tool the agent calls before any monarch__* tool.
    names = {getattr(t, "name", None) for t in registry.tools}
    assert "monarch_ensure_authorized" in names


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
    # Pinned default package + URL, plus the fixed OAuth callback port (positional).
    assert entry["args"] == ["-y", "mcp-remote@0.1.38", "https://api.monarch.com/mcp", "33418"]


def test_port_in_use_detects_a_listener(plugin):
    import socket

    # Bind a real listener, then the probe must report the port as in use; free once closed.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))  # OS picks a free port
        srv.listen()
        port = srv.getsockname()[1]
        assert plugin._port_in_use(port) is True
    assert plugin._port_in_use(port) is False  # listener closed → free again


def test_factory_warns_when_callback_port_busy(plugin, monkeypatch, caplog):
    monkeypatch.setattr(plugin.shutil, "which", lambda _: "/usr/bin/npx")
    monkeypatch.setattr(plugin, "_port_in_use", lambda _p: True)
    with caplog.at_level("WARNING"):
        entry = plugin._server_factory(_cfg({"enabled": True}))
    assert entry is not None  # a busy port warns but never blocks launch
    assert any("already in use" in r.message for r in caplog.records)


def test_factory_quiet_when_callback_port_free(plugin, monkeypatch, caplog):
    monkeypatch.setattr(plugin.shutil, "which", lambda _: "/usr/bin/npx")
    monkeypatch.setattr(plugin, "_port_in_use", lambda _p: False)
    with caplog.at_level("WARNING"):
        plugin._server_factory(_cfg({"enabled": True}))
    assert not any("already in use" in r.message for r in caplog.records)


def test_mcp_factory_respects_custom_url_and_package(plugin, monkeypatch):
    monkeypatch.setattr(plugin.shutil, "which", lambda _: "/usr/bin/npx")
    entry = plugin._server_factory(
        _cfg(
            {
                "enabled": True,
                "url": "https://staging.monarch.test/mcp",
                "package": "mcp-remote@0.1.18",
                "callback_port": 40000,
            }
        )
    )
    assert entry["args"] == ["-y", "mcp-remote@0.1.18", "https://staging.monarch.test/mcp", "40000"]


def test_status_payload_shape(plugin, monkeypatch):
    monkeypatch.setattr(plugin.shutil, "which", lambda _: "/usr/bin/npx")
    monkeypatch.setattr(plugin, "_is_authorized", lambda _url: False)
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
    assert {"enabled", "url", "package", "callback_port", "auth_timeout"} <= keys
    assert m["views"][0]["path"] == "/plugins/monarch-money/view"


# ── pre-auth gate ────────────────────────────────────────────────────────────────────
def _write_tokens(cache_root: Path, url: str, body: dict, version: str = "mcp-remote-9.9.9") -> Path:
    """Drop a tokens file where mcp-remote would cache it: <ver>/<md5(url)>_tokens.json."""
    import hashlib
    import json

    d = cache_root / version
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{hashlib.md5(url.encode()).hexdigest()}_tokens.json"
    f.write_text(json.dumps(body))
    return f


def test_is_authorized_reads_per_url_token(plugin, monkeypatch, tmp_path):
    monkeypatch.setattr(plugin, "OAUTH_CACHE_DIR", tmp_path)
    url = "https://api.monarch.com/mcp"
    assert plugin._is_authorized(url) is False  # empty cache
    # A token for a DIFFERENT url must not count as authorized for ours.
    _write_tokens(tmp_path, "https://other.example/mcp", {"refresh_token": "r"})
    assert plugin._is_authorized(url) is False
    # A refresh_token for OUR url counts even if the access token would be expired.
    _write_tokens(tmp_path, url, {"access_token": "a", "refresh_token": "r"})
    assert plugin._is_authorized(url) is True


def test_is_authorized_ignores_empty_or_keyless_token_file(plugin, monkeypatch, tmp_path):
    monkeypatch.setattr(plugin, "OAUTH_CACHE_DIR", tmp_path)
    url = "https://api.monarch.com/mcp"
    _write_tokens(tmp_path, url, {"scope": "read"})  # no access/refresh token
    assert plugin._is_authorized(url) is False


def test_ensure_authorized_shortcircuits_when_cached(plugin, monkeypatch, tmp_path):
    monkeypatch.setattr(plugin, "OAUTH_CACHE_DIR", tmp_path)
    url = "https://api.monarch.com/mcp"
    _write_tokens(tmp_path, url, {"refresh_token": "r"})

    def _no_spawn(*_a, **_k):  # must NOT launch a browser when already authorized
        raise AssertionError("should not spawn the bridge when a token is cached")

    monkeypatch.setattr(plugin.subprocess, "Popen", _no_spawn)
    out = plugin._ensure_authorized(url=url, pkg="p", port=1, timeout=1, npx="/usr/bin/npx")
    assert out == {"authorized": True, "action": "already-authorized"}


def test_ensure_authorized_primes_then_succeeds(plugin, monkeypatch, tmp_path):
    """Spawns the bridge once; when the token lands, reports authorized and tears it down."""
    monkeypatch.setattr(plugin, "OAUTH_CACHE_DIR", tmp_path)
    monkeypatch.setattr(plugin, "_AUTH_LOCK", tmp_path / ".lock")
    url = "https://api.monarch.com/mcp"

    class _FakeProc:
        def __init__(self, *_a, **_k):
            _write_tokens(tmp_path, url, {"refresh_token": "r"})  # "browser approved" immediately
            self.terminated = False

        def poll(self):
            return None  # still running until we terminate it

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(plugin.subprocess, "Popen", _FakeProc)
    out = plugin._ensure_authorized(url=url, pkg="p", port=1, timeout=5, npx="/usr/bin/npx")
    assert out == {"authorized": True, "action": "authorized"}
    assert not (tmp_path / ".lock").exists(), "lock must be released"


def test_ensure_authorized_waits_when_another_primer_holds_lock(plugin, monkeypatch, tmp_path):
    monkeypatch.setattr(plugin, "OAUTH_CACHE_DIR", tmp_path)
    lock = tmp_path / ".lock"
    monkeypatch.setattr(plugin, "_AUTH_LOCK", lock)
    url = "https://api.monarch.com/mcp"
    lock.write_text("")  # a concurrent (fresh) primer already holds the lock

    def _no_spawn(*_a, **_k):
        raise AssertionError("must not spawn a second bridge while another holds the lock")

    monkeypatch.setattr(plugin.subprocess, "Popen", _no_spawn)
    # No token at entry (so we don't short-circuit); the held primer lands it while we wait.
    monkeypatch.setattr(plugin, "_wait_for_token", lambda _url, _t, proc=None: True)
    out = plugin._ensure_authorized(url=url, pkg="p", port=1, timeout=5, npx="/usr/bin/npx")
    assert out == {"authorized": True, "action": "waited-for-concurrent"}


def test_authorize_impl_guards(plugin, monkeypatch):
    # Disabled connector → a clear ask, no spawn.
    host_off = types.SimpleNamespace(config=lambda: _cfg({"enabled": False}))
    assert "enable" in plugin._authorize_impl(host_off).lower()
    # Enabled but no npx → ask to install Node.
    monkeypatch.setattr(plugin.shutil, "which", lambda _: None)
    host_on = types.SimpleNamespace(config=lambda: _cfg({"enabled": True}))
    assert "node" in plugin._authorize_impl(host_on).lower()


def test_authorize_impl_reports_cached(plugin, monkeypatch):
    monkeypatch.setattr(plugin.shutil, "which", lambda _: "/usr/bin/npx")
    monkeypatch.setattr(plugin, "_is_authorized", lambda _url: True)
    host = types.SimpleNamespace(config=lambda: _cfg({"enabled": True}))
    assert "already authorized" in plugin._authorize_impl(host).lower()
