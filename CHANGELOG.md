# Changelog

## Unreleased

- Development moved to its own repository,
  [alvintayzhenwei/a2a-mcp-connector](https://github.com/alvintayzhenwei/a2a-mcp-connector).
  The package used to live in a subdirectory of the closed source of
  [lvntay.ai](https://lvntay.ai), so nobody could read the code they were being
  asked to `uvx` - the source, its history, its tests and its dependency audits
  are all public now. The package itself is unchanged: same name, same PyPI
  project, same tools.
- CI runs the test suite on Python 3.11 and 3.12, `pip-audit` over the locked
  runtime dependencies (also weekly, so an advisory published against a pinned
  dependency surfaces without a push), and CodeQL static analysis.
- Security: locked `cryptography` 49.0.0 -> 50.0.1. 49.0.0 is affected by
  PYSEC-2026-3552 and reaches this package transitively through `a2a-sdk`. Found
  by the new `pip-audit` job on its first run, which is the point of having one.

## 0.1.1 (2026-09-01)

- **Fixed: `uvx a2a-mcp-connector` was dead on arrival.** The manifest asked for
  `mcp>=1.12` with no upper bound, and mcp 2.0 renamed
  `mcp.server.fastmcp.FastMCP` to `mcp.server.mcpserver.MCPServer` and deleted
  the old path - so once 2.x shipped, every fresh resolve picked it and the
  server died at import with `ModuleNotFoundError: No module named
  'mcp.server.fastmcp'`, registering no tools at all. `mcp` is now capped `<2`,
  which is what the manifest already did for `a2a-sdk` and had simply failed to
  apply to itself. Migrating to mcp 2.x is a deliberate follow-up: move the
  floor to `>=2` rather than widening the range.
- `tests/test_packaging.py` asserts the cap and ties it to the import the server
  actually uses, so changing the code and forgetting the manifest (or the
  reverse) fails in CI instead of in a stranger's terminal. The existing suite
  could not have caught the original break: it runs against a locked dev
  environment that already had mcp 1.x, and only a fresh resolve picks up a new
  major - which is why the test checks published metadata, not the import.

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
