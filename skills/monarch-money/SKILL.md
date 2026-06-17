---
name: monarch-money
description: >-
  Use when the user asks about their personal finances held in Monarch Money —
  net worth, account balances, cash/credit/investment accounts, transactions and
  spending by category or merchant, budgets, cash flow, or recurring bills. Triggers
  on questions like "what's my net worth", "how much did I spend on X", "list my
  accounts", "am I over budget", "show recent transactions". Covers which Monarch
  MCP tools to reach for and the connection/auth caveats. Not for: non-Monarch
  finances, tax advice, or moving money (the connector is read-only).
---

# Monarch Money

This plugin connects the agent to **Monarch Money's official MCP server** (beta) at
`https://api.monarch.com/mcp` via an OAuth bridge. When the plugin is enabled and OAuth
is approved, Monarch's tools appear to the agent **namespaced under `monarch__…`** (e.g.
`monarch__get_accounts`). The exact tool set is whatever Monarch's MCP advertises —
discover it from the live tool list rather than assuming names.

## When to use
Reach for the Monarch tools whenever the user asks about money they track in Monarch:
balances and net worth, account inventories, transactions and spending breakdowns,
budgets vs. actuals, cash flow, or recurring/subscription bills.

## How to answer
1. **Check the tools are present.** If no `monarch__*` tools are bound, the connector
   isn't live — tell the user to enable the plugin in **Settings → Monarch Money** and
   complete the browser OAuth approval (the rail view shows connection status). Don't
   guess figures.
2. **Pick the narrowest tool** for the question (accounts vs. transactions vs. budgets)
   and pass concrete filters — date ranges, category, merchant, account — so you pull
   only what you need rather than dumping the whole ledger into context.
3. **Report real numbers with their as-of context** (account, period, currency). Money
   answers must come from a tool result, never from memory or estimation.
4. **Read-only.** Treat the connector as read-only: summarize, categorize, and analyze.
   Do not attempt to move money, pay bills, or mutate accounts.

## Auth & setup (for troubleshooting)
- Auth is **OAuth** — there is **no API key to paste**. The plugin launches the
  `mcp-remote` bridge (over stdio) which runs Monarch's OAuth flow: on first connect a
  **browser opens** to approve access. Tokens are cached by the bridge (`~/.mcp-auth`),
  then refreshed automatically.
- To connect: enable the plugin in **Settings → Monarch Money**, then complete the
  browser approval that pops up. The rail view shows the connection state
  (Disabled → Node required → Authorize in browser → Connected).
- Access is managed/revoked in the Monarch app under **Settings → Integrations** (not
  in protoAgent).
- Requires **Node/npx** on the host (for `mcp-remote`). If the view says *"Node
  required,"* install Node.js.
- A `401`/auth error from a `monarch__*` tool means the OAuth grant was revoked or the
  cache is stale — re-trigger approval by toggling the connector off/on, or clear
  `~/.mcp-auth`.

## Limits
Monarch's MCP is **beta**: tool names and shapes can change, and rate limits apply.
If a tool is missing or renamed, fall back to whatever related `monarch__*` tool the
live list offers, and tell the user what changed.
