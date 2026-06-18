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
- a **managed MCP server** (``register_mcp_server``) — ``npx -y mcp-remote <url> <port>``
  over stdio; started only when the connector is enabled (and ``npx`` is on PATH), else the
  factory returns ``None`` and nothing connects. Monarch's tools then appear to the
  agent namespaced as ``monarch__*``;
- a **pre-auth gate tool** (``monarch_ensure_authorized``) the agent calls *first* to
  establish OAuth **once** — opening a single browser tab and blocking until approval is
  cached — so a burst of ``monarch__*`` calls can't each spawn their own auth tab;
- a **console view** (left-rail dashboard) showing connection status + onboarding;
- a gated **status route** (``GET /api/plugins/monarch-money/status``) the view reads.

The multi-tab problem is a cold-start race, not token expiry: mcp-remote caches tokens in
``~/.mcp-auth`` and refreshes the access token silently, so routine expiry never reprompts.
The fixes are (1) a **fixed OAuth callback port** so mcp-remote dedupes concurrent instances
to one browser, and (2) the gate tool, which primes the token before any tool call fans out.

Config/Settings come from the manifest (ADR 0019): the ``monarch_money`` section holds
``enabled`` (bool), ``url`` (the MCP endpoint), and ``package`` (the bridge's npm spec).
There is **no secret** — OAuth tokens live in the bridge's own cache, not our config.

Requires **Node/npx** on the host (for ``mcp-remote``). The first connect opens a
browser, so an operator must be present to approve it once.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

log = logging.getLogger("protoagent.plugins.monarch_money")

CONFIG_SECTION = "monarch_money"
DEFAULT_MCP_URL = "https://api.monarch.com/mcp"
DEFAULT_BRIDGE_PKG = "mcp-remote@0.1.38"  # pinned npm OAuth-bridge spec (npx -y <pkg> <url> <port>)
OAUTH_CACHE_DIR = Path.home() / ".mcp-auth"  # where mcp-remote caches OAuth tokens
# A *fixed* OAuth redirect port is the lever that stops the multi-tab problem: mcp-remote
# coordinates concurrent instances (lockfile + long-poll) keyed on URL hash + this port, so
# a burst of cold-start connections dedupes to a SINGLE browser approval instead of one tab
# each. We pass the same port to both the managed bridge and the pre-auth primer below.
DEFAULT_CALLBACK_PORT = 33418
DEFAULT_AUTH_TIMEOUT_S = 180  # how long the pre-auth gate waits for the user's browser approval
_AUTH_LOCK = OAUTH_CACHE_DIR / ".monarch-authorize.lock"  # cross-process guard: one primer at a time


def _mcfg(config) -> dict:
    """This plugin's resolved config section from a live LangGraphConfig (ADR 0019)."""
    return (getattr(config, "plugin_config", {}) or {}).get(CONFIG_SECTION, {}) or {}


def _callback_port(m: dict) -> int:
    """The fixed OAuth redirect port (config ``callback_port``), falling back to the default."""
    try:
        return int(m.get("callback_port") or DEFAULT_CALLBACK_PORT)
    except (TypeError, ValueError):
        return DEFAULT_CALLBACK_PORT


def _auth_timeout(m: dict) -> float:
    """Seconds the pre-auth gate blocks waiting for browser approval (config ``auth_timeout``)."""
    try:
        return float(m.get("auth_timeout") or DEFAULT_AUTH_TIMEOUT_S)
    except (TypeError, ValueError):
        return float(DEFAULT_AUTH_TIMEOUT_S)


def _port_in_use(port: int) -> bool:
    """Best-effort: is something already listening on ``127.0.0.1:<port>``?

    Probes the loopback bind the way ``mcp-remote`` does for its OAuth callback. A clash means
    mcp-remote falls back to a *random* port, losing the single-browser coordination — so we
    only warn (never block). On any unexpected error we assume free (don't cry wolf).
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True
        except Exception:  # noqa: BLE001 — a probe must never break bridge launch
            return False


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
    port = _callback_port(m)
    if _port_in_use(port):
        log.warning(
            "[monarch-money] OAuth callback port %d is already in use on 127.0.0.1 — mcp-remote "
            "will fall back to a random port, which can re-introduce multiple auth tabs. Set a free "
            "`callback_port` in the monarch_money config.",
            port,
        )
    return {
        "name": "monarch",
        "enabled": True,
        "transport": "stdio",
        "command": npx,
        # Pin the OAuth callback port (positional, after the URL) so this managed bridge and
        # the pre-auth primer share mcp-remote's single-browser coordination — see DEFAULT_CALLBACK_PORT.
        "args": ["-y", pkg, url, str(port)],
    }


def _server_url_hash(url: str) -> str:
    """mcp-remote keys its token cache by md5 of the server URL (``getServerUrlHash``)."""
    return hashlib.md5(url.encode("utf-8")).hexdigest()


def _tokens_file(url: str) -> Path | None:
    """The cached OAuth *tokens* file for ``url``, if one exists with a usable token.

    mcp-remote stores tokens at ``~/.mcp-auth/mcp-remote-<ver>/<md5(url)>_tokens.json``. We
    scan every version dir (the cache survives bridge bumps) and accept a file that carries a
    ``refresh_token`` (or ``access_token``): an *expired* access token still counts as
    authorized because mcp-remote refreshes it silently — no browser needed. Only a revoked
    grant or a cleared cache forces a fresh browser approval.
    """
    h = _server_url_hash(url)
    try:
        version_dirs = sorted(OAUTH_CACHE_DIR.glob("mcp-remote-*"))
    except OSError:
        return None
    for d in version_dirs:
        f = d / f"{h}_tokens.json"
        try:
            data = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and (data.get("refresh_token") or data.get("access_token")):
            return f
    return None


def _is_authorized(url: str) -> bool:
    """Has the bridge cached a usable OAuth token for ``url`` (i.e. no browser needed)?"""
    return _tokens_file(url) is not None


def _acquire_lock(path: Path, *, stale_after: float) -> int | None:
    """Atomically take a cross-process lock, or ``None`` if another live primer holds it.

    A lock left by a crashed primer (older than ``stale_after``) is stolen so we never wedge.
    """
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        return os.open(str(path), flags, 0o600)
    except FileExistsError:
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            return None
        if age <= stale_after:
            return None  # someone else is actively running the OAuth flow — wait for them
        try:
            path.unlink()
            return os.open(str(path), flags, 0o600)
        except OSError:
            return None


def _release_lock(fd: int, path: Path) -> None:
    try:
        os.close(fd)
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def _wait_for_token(url: str, timeout: float, proc: subprocess.Popen | None = None) -> bool:
    """Poll the cache until a token lands for ``url`` (or ``timeout`` / the primer exits)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _is_authorized(url):
            return True
        if proc is not None and proc.poll() is not None:
            return _is_authorized(url)  # primer exited — last look, then give up
        time.sleep(1.0)
    return _is_authorized(url)


def _ensure_authorized(*, url: str, pkg: str, port: int, timeout: float, npx: str) -> dict:
    """Make sure a Monarch OAuth token is cached, opening **at most one** browser tab.

    Idempotent and concurrency-safe. Returns at once if already authorized; otherwise takes a
    cross-process lock so that even if several callers (or the managed bridge) race at cold
    start, exactly one runs the browser flow and the rest wait for the shared token to land.
    Returns ``{"authorized": bool, "action": str}``.
    """
    if _is_authorized(url):
        return {"authorized": True, "action": "already-authorized"}

    try:
        OAUTH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.warning("[monarch-money] can't create OAuth cache dir %s: %s", OAUTH_CACHE_DIR, e)

    fd = _acquire_lock(_AUTH_LOCK, stale_after=timeout + 60)
    if fd is None:
        ok = _wait_for_token(url, timeout)
        return {"authorized": ok, "action": "waited-for-concurrent" if ok else "timeout"}

    proc: subprocess.Popen | None = None
    try:
        # Spawn the bridge purely to run OAuth; it opens one browser tab and, on approval,
        # writes the token to the shared cache. We tear it down once the token is on disk —
        # the managed bridge then reads that cached token and never reprompts.
        proc = subprocess.Popen(
            [npx, "-y", pkg, url, str(port)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        ok = _wait_for_token(url, timeout, proc=proc)
        if ok:
            action = "authorized"
        elif proc.poll() is not None:
            action = "bridge-exited"
        else:
            action = "timeout"
        return {"authorized": ok, "action": action}
    except OSError as e:
        log.warning("[monarch-money] failed to launch OAuth primer: %s", e)
        return {"authorized": False, "action": "launch-failed"}
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        _release_lock(fd, _AUTH_LOCK)


def _authorize_impl(host) -> str:
    """Back the agent tool: establish Monarch OAuth once, returning a line the agent acts on."""
    cfg = host.config() if host and getattr(host, "config", None) else None
    m = _mcfg(cfg)
    if not m.get("enabled"):
        return "Monarch Money isn't enabled. Ask the user to turn it on in Settings → Monarch Money, then retry."
    npx = shutil.which("npx")
    if not npx:
        return "Can't authorize: Node/`npx` isn't on PATH. Ask the user to install Node.js so the OAuth bridge can run."
    url = (m.get("url") or "").strip() or DEFAULT_MCP_URL
    if _is_authorized(url):
        return "Monarch is already authorized (cached OAuth token). Go ahead and call the monarch__* tools."
    pkg = (m.get("package") or "").strip() or DEFAULT_BRIDGE_PKG
    result = _ensure_authorized(url=url, pkg=pkg, port=_callback_port(m), timeout=_auth_timeout(m), npx=npx)
    if result["authorized"]:
        return (
            "Monarch authorization complete — a single browser approval was captured and "
            "cached. You can now call the monarch__* tools; they won't reopen the browser."
        )
    if result["action"] == "timeout":
        return (
            "A browser tab opened to approve Monarch access but approval wasn't completed in "
            "time. Ask the user to finish it in the open tab, then call this tool again."
        )
    return (
        "Couldn't establish Monarch authorization — the OAuth bridge exited before a token "
        "was cached. Ask the user to retry; if it persists, check Node/npx and the MCP URL."
    )


def _status_payload(host) -> dict:
    """Connection status for the view — booleans + the (non-secret) URL only."""
    cfg = host.config() if host and getattr(host, "config", None) else None
    m = _mcfg(cfg)
    enabled = bool(m.get("enabled"))
    npx_available = shutil.which("npx") is not None
    url = (m.get("url") or "").strip() or DEFAULT_MCP_URL
    return {
        "enabled": enabled,
        "npx_available": npx_available,
        "authorized": _is_authorized(url),
        "connected": enabled and npx_available,
        "url": url,
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
    from langchain_core.tools import tool

    host = getattr(registry, "host", None)
    registry.register_mcp_server(_server_factory)

    @tool
    def monarch_ensure_authorized() -> str:
        """Establish Monarch Money's OAuth authorization ONCE, before any `monarch__*` tools.

        Call this first — and await it — at the start of any task that will read Monarch data
        (net worth, accounts, transactions, budgets). If access is already authorized it
        returns immediately; otherwise it opens a SINGLE browser tab for the user to approve
        and blocks until that approval is cached. This is what prevents a burst of monarch__*
        calls from each spawning its own auth tab — so do NOT fan out monarch__* calls until
        this returns success. Idempotent and safe to call again (e.g. after a 401, which means
        the grant was revoked and a fresh approval is needed).
        """
        return _authorize_impl(host)

    registry.register_tool(monarch_ensure_authorized)
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
