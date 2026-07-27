# a2a-mcp-connector

A **generic MCP server that stream-connects an AI agent to any standard
[A2A](https://a2aprotocol.ai/) endpoint** — plus a built-in A2A Agent Card
**validator**. Install it once in your MCP-capable agent (Claude Code, Codex,
Cursor, …) and you can validate and drive **any** A2A agent over the standard
`input-required` multi-turn protocol, with **no manual `a2a-sdk` install and no
per-endpoint code**.

It is deliberately **provider-agnostic**: there is no LLM and no API key inside
this server. It is a pure standard-A2A **client bridge** — your agent is the
intelligence; the connector only speaks A2A. (Contrast with LLM-bound bridges
that wire in a specific provider such as OpenRouter — this one binds to none.)

## Install

```bash
# Claude Code
claude mcp add a2a-connector -- uvx a2a-mcp-connector
# Codex
codex mcp add a2a-connector -- uvx a2a-mcp-connector
```

Any other MCP-aware client (Cursor, Claude Desktop, a hand-edited `.mcp.json`):

```json
{
  "mcpServers": {
    "a2a-connector": {
      "type": "stdio",
      "command": "uvx",
      "args": ["a2a-mcp-connector"]
    }
  }
}
```

Before the package is published to PyPI, run it from a local checkout instead:
`uv run a2a-mcp-connector` (from this directory) or
`uvx --from ./a2a-mcp-connector/dist/<wheel> a2a-mcp-connector`.

## Tools

| Tool | What it does |
|------|--------------|
| `a2a_validate(agent_card_url, bearer="")` | Fetch a remote Agent Card and return a standard-A2A validation report (valid?/checks/warnings) — parses it against the a2a-sdk model and checks the required spec fields + streaming capability. Nothing is connected. |
| `a2a_connect(agent_card_url, bearer="")` | Validate the card first (refuses on failure), then start a background streaming session. Returns immediately. |
| `a2a_wait_turn(max_seconds=30)` | Wait (bounded) for the next `input-required` prompt; returns it, "no turn yet", "the session is over.", or an error string. |
| `a2a_send(message)` | Submit your reply to the current turn. |
| `a2a_status()` | Connected? / turn pending? / done? / error snapshot. |
| `a2a_leave()` | Disconnect and close the session cleanly. |

`bearer` is **optional** — pass it only when the endpoint's card/RPC route
requires auth. It is held in memory only and never logged or returned by any
tool.

## Flow

```
a2a_validate <card-url>            # optional pre-flight; a2a_connect validates anyway
a2a_connect  <card-url> <bearer?>  # start the session
loop:
  a2a_wait_turn                    # show the prompt to your human, get their reply
  a2a_send    <their reply>        # submit it
a2a_leave                          # when done (or "the session is over.")
```

The connecting agent should **present each turn's prompt to its human and wait
for their actual choice** — it must not invent a reply.

## How it works

One A2A task maps to the whole interaction. `a2a_client.A2ASession` resolves the
Agent Card, derives the JSON-RPC URL from the served card path (reverse-proxy
safe — it does not trust the card's advertised interface URL), opens or resumes
the task, and drives turns with `message/send`. Because an A2A turn blocks until
it is genuinely your turn, the session runs on one background asyncio task and
the MCP tools are small, bounded polls over it — so a tool call never hangs.

`validator.validate_agent_card` fetches the raw card, parses it into the a2a-sdk
`AgentCard` model (the authoritative "can this connector drive it?" check), then
runs the standard-A2A structural checks (required fields, tolerant of both the
`supportedInterfaces` and legacy top-level `url` shapes) and reports the
`capabilities.streaming` capability.

## Standalone

This package imports neither `a2a_games` nor `a2a_raid_mcp` (its game-specific
sibling) — its only dependencies are the public `mcp`, `a2a-sdk` (pinned
`==1.1.0`), and `httpx` — so it is independently publishable to PyPI and reusable
against any A2A endpoint.

## License

MIT © 2026 Alvin Tay
