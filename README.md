# Monarch Money — protoAgent plugin

Personal-finance tools for [protoAgent](https://github.com/protoLabsAI/protoAgent),
backed by **Monarch Money's official MCP server** (beta). Enable it and the agent
can answer questions about your accounts, balances, transactions, spending, and
budgets — read-only.

## What it contributes

- **A managed MCP server** — the plugin connects the agent to Monarch's hosted MCP
  endpoint (`https://api.monarch.com/mcp`), authed with a bearer token. Monarch's
  tools then appear to the agent namespaced as `monarch__…` (e.g.
  `monarch__get_accounts`). No subprocess, no `credentials.json` — just a token.
- **A console view** — a left-rail "Monarch Money" dashboard showing connection
  status and example prompts.
- **A skill** — `skills/monarch-money/SKILL.md`, telling the agent when and how to
  use the Monarch tools (and the auth caveats).

## Install & enable

```bash
# From a running protoAgent (or the console Plugins panel):
python -m server plugin install https://github.com/protoLabsAI/monarch-money-plugin
```

Then, in **Settings → Monarch Money**:

1. Toggle **Enable Monarch Money** on.
2. Paste your **Monarch API token** (stored in `secrets.yaml`, never tracked).
3. Save — the managed MCP server connects live; the rail view shows **Connected**.

**install ≠ enable ≠ trust.** Installing only fetches code; enabling runs it
in-process with the agent's privileges. Only enable plugins you trust.

### Getting your token

Open the Monarch web app → DevTools (F12) → **Application → Local Storage** and copy
the `token` value (or grab the `Authorization: Token …` header from any API request
in the Network tab). Monarch's **MCP Connector** help article also issues a key.

**MFA accounts:** Monarch needs the raw **Base32 secret** (the seed from authenticator
setup), not a rotating 6-digit code.

## Config

| Key       | Type   | Default                          | Notes                                  |
|-----------|--------|----------------------------------|----------------------------------------|
| `enabled` | bool   | `false`                          | Gate the MCP server (also needs token) |
| `token`   | secret | —                                | Monarch API token → `secrets.yaml`     |
| `url`     | string | `https://api.monarch.com/mcp`    | Monarch's MCP endpoint                 |

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
