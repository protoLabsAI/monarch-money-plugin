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
`https://api.monarch.com/mcp`. When the plugin is enabled and an API token is set,
Monarch's tools appear to the agent **namespaced under `monarch__…`** (e.g.
`monarch__get_accounts`). The exact tool set is whatever Monarch's MCP advertises —
discover it from the live tool list rather than assuming names.

## When to use
Reach for the Monarch tools whenever the user asks about money they track in Monarch:
balances and net worth, account inventories, transactions and spending breakdowns,
budgets vs. actuals, cash flow, or recurring/subscription bills.

## How to answer
1. **Check the tools are present.** If no `monarch__*` tools are bound, the connector
   isn't live — tell the user to enable the plugin and set their token in
   **Settings → Monarch Money** (the rail view shows connection status). Don't guess
   figures.
2. **Pick the narrowest tool** for the question (accounts vs. transactions vs. budgets)
   and pass concrete filters — date ranges, category, merchant, account — so you pull
   only what you need rather than dumping the whole ledger into context.
3. **Report real numbers with their as-of context** (account, period, currency). Money
   answers must come from a tool result, never from memory or estimation.
4. **Read-only.** Treat the connector as read-only: summarize, categorize, and analyze.
   Do not attempt to move money, pay bills, or mutate accounts.

## Auth & setup (for troubleshooting)
- Auth is a **static bearer token** the operator pastes into Settings; it's stored in
  `secrets.yaml` and injected as `Authorization: Bearer <token>`.
- The user finds their token in the Monarch web app (DevTools → Application →
  Local Storage, the `token` entry; or the `Authorization: Token …` header on any
  API request). Monarch's **MCP Connector** help page also issues a connector key.
- **MFA accounts:** Monarch needs the raw **Base32 secret** (the seed used to set up
  your authenticator), not a rotating 6-digit code.
- A `401`/auth error from a `monarch__*` tool means the token is missing, expired, or
  wrong — point the user back to Settings to refresh it.

## Limits
Monarch's MCP is **beta**: tool names and shapes can change, and rate limits apply.
If a tool is missing or renamed, fall back to whatever related `monarch__*` tool the
live list offers, and tell the user what changed.
