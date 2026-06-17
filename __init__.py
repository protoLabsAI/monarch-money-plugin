"""Monarch Money — personal finance via the official Monarch MCP server (beta).

The official endpoint (``https://api.monarch.com/mcp``) authenticates with **OAuth**,
not a static key: on first connect you approve access in your browser, and access is
managed/revoked under Monarch → Settings → Integrations. protoAgent's MCP client only
speaks static-header auth to a remote ``streamable_http`` server, so we don't point at
the endpoint directly. Instead we launch the **``mcp-remote`` OAuth bridge** as a
managed **stdio** MCP server: it runs the browser OAuth flow once, caches the tokens
locally (``~/.mcp-auth``), and proxies the remote server over stdio — which protoAgent
*does* support (structurally the same as how the Google plugin runs a subprocess).

Contributes:
- a **managed MCP server** (``register_mcp_server``) — ``npx -y mcp-remote <url>`` over
  stdio; started only when the connector is enabled (and ``npx`` is on PATH), else the
  factory returns ``None`` and nothing connects. Monarch's tools then appear to the
  agent namespaced as ``monarch__*``;
- a **console view** (left-rail dashboard) showing connection status + onboarding;
- a gated **status route** (``GET /api/plugins/monarch-money/status``) the view reads.

Config/Settings come from the manifest (ADR 0019): the ``monarch_money`` section holds
``enabled`` (bool), ``url`` (the MCP endpoint), and ``package`` (the bridge's npm spec).
There is **no secret** — OAuth tokens live in the bridge's own cache, not our config.

Requires **Node/npx** on the host (for ``mcp-remote``). The first connect opens a
browser, so an operator must be present to approve it once.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

log = logging.getLogger("protoagent.plugins.monarch_money")

CONFIG_SECTION = "monarch_money"
DEFAULT_MCP_URL = "https://api.monarch.com/mcp"
DEFAULT_BRIDGE_PKG = "mcp-remote@0.1.38"  # pinned npm OAuth-bridge spec (npx -y <pkg> <url>)
OAUTH_CACHE_DIR = Path.home() / ".mcp-auth"  # where mcp-remote caches OAuth tokens


def _mcfg(config) -> dict:
    """This plugin's resolved config section from a live LangGraphConfig (ADR 0019)."""
    return (getattr(config, "plugin_config", {}) or {}).get(CONFIG_SECTION, {}) or {}


def _server_factory(config) -> dict | None:
    """Build the managed Monarch MCP server entry, or ``None`` when off / no npx.

    Called at every graph build with the live config. We don't connect to the OAuth
    endpoint directly — we launch the ``mcp-remote`` bridge over **stdio**; it performs
    the OAuth browser flow on first connect and proxies the remote server. Returning
    ``None`` keeps MCP from launching a bridge the operator hasn't opted into (or that
    can't run because Node/``npx`` is missing).
    """
    m = _mcfg(config)
    if not m.get("enabled"):
        return None
    npx = shutil.which("npx")
    if not npx:
        log.warning("[monarch-money] enabled but `npx` not found on PATH — install Node to use the OAuth bridge")
        return None
    url = (m.get("url") or "").strip() or DEFAULT_MCP_URL
    pkg = (m.get("package") or "").strip() or DEFAULT_BRIDGE_PKG
    return {
        "name": "monarch",
        "enabled": True,
        "transport": "stdio",
        "command": npx,
        "args": ["-y", pkg, url],
    }


def _has_oauth_cache() -> bool:
    """Best-effort: has the bridge cached OAuth tokens (i.e. authorized at least once)?"""
    try:
        return OAUTH_CACHE_DIR.is_dir() and any(OAUTH_CACHE_DIR.iterdir())
    except OSError:
        return False


def _status_payload(host) -> dict:
    """Connection status for the view — booleans + the (non-secret) URL only."""
    cfg = host.config() if host and getattr(host, "config", None) else None
    m = _mcfg(cfg)
    enabled = bool(m.get("enabled"))
    npx_available = shutil.which("npx") is not None
    return {
        "enabled": enabled,
        "npx_available": npx_available,
        "authorized": _has_oauth_cache(),
        "connected": enabled and npx_available,
        "url": (m.get("url") or "").strip() or DEFAULT_MCP_URL,
    }


def _build_data_router(host):
    """Gated DATA routes (mounted under /api/plugins/monarch-money → operator bearer)."""
    from fastapi import APIRouter

    router = APIRouter()

    @router.get("/status")
    async def _status():
        """Whether the Monarch connector is enabled, npx is present, and OAuth is cached."""
        return _status_payload(host)

    return router


def _build_page_router():
    """The PUBLIC view page (an iframe page-load can't carry a bearer; its data is gated)."""
    from fastapi import APIRouter
    from fastapi.responses import HTMLResponse

    router = APIRouter()

    @router.get("/view")
    async def _view():
        return HTMLResponse(_VIEW_HTML)

    return router


def register(registry) -> None:
    """Wire this plugin's contributions into the agent (ADR 0018)."""
    host = getattr(registry, "host", None)
    registry.register_mcp_server(_server_factory)
    registry.register_router(_build_page_router(), prefix="/plugins/monarch-money")
    registry.register_router(_build_data_router(host), prefix="/api/plugins/monarch-money")
    registry.register_skill_dir("skills")  # bundle the monarch-money SKILL.md


# ── The view (a sandboxed iframe of this page; copies the chat_example four rules) ──
_VIEW_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>Monarch Money</title>
<script>
  "use strict";
  // RULE 3: slug-aware base, computed first (host: "" · proxied: /agents/<slug>).
  window.__base = location.pathname.split("/plugins/")[0];
  // RULE 4: link the design-system kit from the console's own same-origin DS.
  document.write('<link rel="stylesheet" href="' + window.__base + '/_ds/plugin-kit.css">');
</script>
<style>
  html, body { margin: 0; height: 100%; }
  body { padding: 28px 32px; box-sizing: border-box;
    background: var(--pl-color-bg); color: var(--pl-color-fg);
    font-family: var(--pl-font-sans, system-ui); }
  h1 { font-size: 18px; margin: 0 0 4px; display: flex; align-items: center; gap: 10px; }
  .sub { color: var(--pl-color-fg-muted); font-size: 13px; margin: 0 0 22px; }
  .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--pl-color-fg-muted); }
  .dot.ok { background: var(--pl-color-status-success, #3fb950); }
  .dot.warn { background: var(--pl-color-status-warning, #d29922); }
  .dot.off { background: var(--pl-color-status-error, #f85149); }
  .card { border: var(--pl-border-width) solid var(--pl-color-border);
    border-radius: var(--pl-radius); background: var(--pl-color-bg-raised);
    padding: 16px 18px; margin-bottom: 14px; }
  .card h2 { font-size: 14px; margin: 0 0 8px; }
  .card p { font-size: 13px; line-height: 1.6; margin: 6px 0; color: var(--pl-color-fg-muted); }
  code { background: var(--pl-color-bg); padding: 1px 5px; border-radius: 4px;
    font-family: var(--pl-font-mono, ui-monospace, monospace); font-size: 12px; }
  .status { display: flex; align-items: center; gap: 10px; font-size: 14px; font-weight: 600; }
  ul { margin: 6px 0; padding-left: 20px; }
  li { font-size: 13px; line-height: 1.7; color: var(--pl-color-fg-muted); }
</style>
</head>
<body>
  <h1><span id="dot" class="dot"></span> Monarch Money</h1>
  <p class="sub">Personal finance over Monarch's official MCP server (beta), via OAuth.</p>

  <div class="card">
    <h2>Connection</h2>
    <div class="status"><span id="state">Checking…</span></div>
    <p id="hint"></p>
  </div>

  <div class="card">
    <h2>Try it</h2>
    <p>Once connected, ask the agent things like:</p>
    <ul>
      <li>“What's my current net worth?”</li>
      <li>“List my accounts and balances.”</li>
      <li>“How much did I spend on dining last month?”</li>
      <li>“Am I over budget in any category this month?”</li>
    </ul>
  </div>

<script type="module">
  "use strict";
  const kit = await import(window.__base + "/_ds/plugin-kit.js");
  kit.initPluginView(); // RULE 4: bearer + theme handshake

  const dot = document.getElementById("dot");
  const state = document.getElementById("state");
  const hint = document.getElementById("hint");

  async function refresh() {
    try {
      // RULE 2 + 3: gated, slug-aware data call — no bearer in the URL, no hardcoded host.
      const res = await kit.apiFetch("/api/plugins/monarch-money/status");
      if (!res.ok) throw new Error(res.status + " " + res.statusText);
      const s = await res.json();
      if (!s.enabled) {
        dot.className = "dot off";
        state.textContent = "Disabled";
        hint.innerHTML = "Enable the connector in <b>Settings → Monarch Money</b>.";
      } else if (!s.npx_available) {
        dot.className = "dot off";
        state.textContent = "Node required";
        hint.innerHTML = "Enabled, but <code>npx</code> isn't on PATH. Install <b>Node.js</b> so the OAuth bridge (<code>mcp-remote</code>) can run.";
      } else if (!s.authorized) {
        dot.className = "dot warn";
        state.textContent = "Authorize in browser";
        hint.innerHTML = "Enabled. On first connect a browser opens to approve access at <code>" + s.url + "</code>. Manage/revoke under Monarch → Settings → Integrations.";
      } else {
        dot.className = "dot ok";
        state.textContent = "Connected";
        hint.innerHTML = "Authorized via OAuth at <code>" + s.url + "</code>. The agent can query your finances.";
      }
    } catch (err) {
      dot.className = "dot off";
      state.textContent = "Unavailable";
      hint.textContent = "Couldn't read status: " + (err && err.message ? err.message : err);
    }
  }

  // Performance rule: refresh on load + when the tab is shown; never poll a hidden window.
  void refresh();
  document.addEventListener("visibilitychange", () => { if (!document.hidden) void refresh(); });
</script>
</body>
</html>
"""
