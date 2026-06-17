# Monarch Money — protoAgent plugin

Personal-finance tools for [protoAgent](https://github.com/protoLabsAI/protoAgent),
backed by **Monarch Money's official MCP server** (beta). Enable it and the agent
can answer questions about your accounts, balances, transactions, spending, and
budgets — read-only.

## What it contributes

- **A managed MCP server** — the plugin connects the agent to Monarch's hosted MCP
  endpoint (`https://api.monarch.com/mcp`) via **OAuth**. Because protoAgent's MCP
  client speaks only static-header auth to a remote server, the plugin instead launches
  the [`mcp-remote`](https://www.npmjs.com/package/mcp-remote) **OAuth bridge** as a
  managed **stdio** server (`npx -y mcp-remote <url>`): it runs the browser OAuth flow
  once, caches the tokens (`~/.mcp-auth`), and proxies the remote server. Monarch's
  tools then appear to the agent namespaced as `monarch__…` (e.g. `monarch__get_accounts`).
- **A console view** — a left-rail "Monarch Money" dashboard showing connection
  status and example prompts.
- **A skill** — `skills/monarch-money/SKILL.md`, telling the agent when and how to
  use the Monarch tools (and the auth caveats).

## Requirements

- **Node.js / `npx`** on the host (to run the `mcp-remote` bridge).
- An operator present at a browser for the **one-time OAuth approval** on first connect.

## Install & enable

```bash
# From a running protoAgent (or the console Plugins panel):
python -m server plugin install https://github.com/protoLabsAI/monarch-money-plugin
```

Then, in **Settings → Monarch Money**:

1. Toggle **Enable Monarch Money** on and save.
2. On first connect a **browser opens** — sign in to Monarch and approve access. You
   never paste a key or password.
3. The rail view walks the state: Disabled → Node required → **Authorize in browser** →
   **Connected**.

Manage or revoke access later in the Monarch app under **Settings → Integrations**.

**install ≠ enable ≠ trust.** Installing only fetches code; enabling runs it
in-process with the agent's privileges. Only enable plugins you trust.

### Why OAuth, not an API key

Monarch's official MCP connector authenticates via **OAuth** — there is no API key in
the Monarch UI to copy. (The "token from DevTools → Local Storage" trick belongs to
*unofficial* community servers that hit Monarch's private API; this plugin uses the
official endpoint + OAuth instead.)

## Config

| Key       | Type   | Default                          | Notes                                       |
|-----------|--------|----------------------------------|---------------------------------------------|
| `enabled` | bool   | `false`                          | Gate the OAuth-bridge MCP server            |
| `url`     | string | `https://api.monarch.com/mcp`    | Monarch's MCP endpoint                      |
| `package` | string | `mcp-remote@0.1.38`              | pinned npm spec for the bridge; override to bump |

No secret is stored — OAuth tokens live in the bridge's own cache (`~/.mcp-auth`).

## Develop

```bash
uv venv .venv && uv pip install -p .venv -r requirements-dev.txt ruff
.venv/bin/python -m pytest -q       # host-free suite (no protoAgent needed)
.venv/bin/ruff check . && .venv/bin/ruff format --check tests/
```

The test suite is host-free via the vendored testkit (`tests/_plugin_testkit.py`),
so CI (`.github/workflows/ci.yml`) runs green with no protoAgent checkout.

## Caveats

Monarch's MCP is **beta** — tool names/shapes may change and rate limits apply. The
connector is **read-only**: the agent can analyze your finances but not move money.
