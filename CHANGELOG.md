# Changelog

## 0.1.0 (2026-07-27)

Initial release.

- Generic MCP server (`a2a-connector`) to stream-connect an AI agent to any
  standard A2A endpoint over the `input-required` multi-turn protocol.
- Tools: `a2a_validate`, `a2a_connect`, `a2a_wait_turn`, `a2a_send`,
  `a2a_status`, `a2a_leave`.
- Built-in standard-A2A Agent Card validator (`a2a_validate`): a2a-sdk-model
  parse + required-field + streaming-capability checks, tolerant of both the
  `supportedInterfaces` and legacy top-level `url` card shapes.
- Provider-agnostic: no LLM, no API key. Pure standard-A2A client bridge.
- Bearer optional and header-only; never logged or returned by any tool.
- Standalone/publishable: depends only on `mcp`, `a2a-sdk==1.1.0`, `httpx`.
