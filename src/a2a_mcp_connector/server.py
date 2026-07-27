"""a2a-mcp-connector: a GENERIC MCP server that lets an AI agent stream-connect
to ANY standard A2A endpoint, plus a built-in A2A Agent Card validator.

Provider-agnostic: unlike an LLM-bound bridge, this server has NO model and no
API key — it is a pure standard-A2A CLIENT. The calling agent (Claude / Codex /
Cursor, via MCP) is the intelligence; the connector only speaks A2A.

A2A turns BLOCK — the remote agent only returns your next `input-required`
prompt when it is genuinely your turn, and `A2ASession.open()`/`reply()` block
(over a long-held HTTP request) until then. An MCP tool call must never hang
unboundedly, so the session runs on a BACKGROUND driver task and the tools are
small poll-style calls over a bit of module-level state:

    a2a_validate  -> fetch + validate a remote Agent Card, return a report
    a2a_connect   -> validate, then start the background driver, return now
    a2a_wait_turn -> wait (bounded by max_seconds) for the next pending prompt
    a2a_send      -> hand a reply to the driver and unblock it
    a2a_status    -> a quick connected?/turn?/done?/error snapshot
    a2a_leave     -> cancel the driver and close the session

One session per process (module globals) — a stdio MCP server serves one client
at a time; a second `a2a_connect` replaces the first. The bearer (if any) lives
only in memory and never appears in a tool's return value.
"""
from __future__ import annotations

import asyncio
from typing import Any

from mcp.server.fastmcp import FastMCP

from a2a_mcp_connector.a2a_client import DONE, A2ASession
from a2a_mcp_connector.validator import validate_agent_card

mcp = FastMCP("a2a-connector")

# ---------------------------------------------------------------------------
# Module-level session state (single active session per process).
# ---------------------------------------------------------------------------
_session: Any = None
_driver: "asyncio.Task[None] | None" = None
_pending_prompt: str | None = None
_prompt_event: asyncio.Event = asyncio.Event()
_move_queue: "asyncio.Queue[str]" = asyncio.Queue()
_done: bool = False
_error: str | None = None


def _reset_state() -> None:
    """Reset all module-level session state to a fresh, disconnected baseline.
    Best-effort cancels a still-running driver (fire-and-forget — callers that
    must be sure the old driver finished, e.g. `a2a_leave`, await it first)."""
    global _session, _driver, _pending_prompt, _prompt_event, _move_queue, _done, _error
    if _driver is not None and not _driver.done():
        _driver.cancel()
    _session = None
    _driver = None
    _pending_prompt = None
    _prompt_event = asyncio.Event()
    _move_queue = asyncio.Queue()
    _done = False
    _error = None


async def _run_driver() -> None:
    """Drive the whole A2A session in the background: open it, then loop
    reply-for-next-prompt until the task goes terminal. Each iteration blocks on
    `queue.get()` — i.e. on `a2a_send` actually being called — so this task only
    ever advances one human-in-the-loop turn at a time.

    `session`/`queue` are bound as LOCALS at the top (not re-read from globals on
    every iteration): if a fresh `a2a_connect` installs a new session/queue while
    this (cancelled) task is still unwinding, a resumed step must keep talking to
    the session/queue it started with, never the new one."""
    global _pending_prompt, _done, _error
    session = _session
    queue = _move_queue
    try:
        prompt = await session.open()
        while prompt != DONE:
            _pending_prompt = prompt
            _prompt_event.set()
            move = await queue.get()
            prompt = await session.reply(move)
        _done = True
        _pending_prompt = None
        _prompt_event.set()
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 - surfaced to the caller as a plain string
        _error = f"{type(e).__name__}: {e}"
        _prompt_event.set()


async def _teardown() -> None:
    """Await-cancel the current driver and close the current session, then reset
    state. Shared by `a2a_connect` (torn down BEFORE a fresh connect replaces it,
    so a 2nd connect without a leave never leaks the old httpx client/driver) and
    `a2a_leave`."""
    global _driver, _session
    if _driver is not None:
        _driver.cancel()
        try:
            await _driver
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 - the old driver's own error is irrelevant now
            pass
    if _session is not None:
        await _session.close()
    _reset_state()


def _format_report(report: dict) -> str:
    """Human-readable one-block summary of a validator report (bearer never in it)."""
    lines: list[str] = []
    verdict = "VALID ✓" if report.get("valid") else "INVALID ✗"
    lines.append(f"A2A Agent Card: {verdict}  ({report.get('url', '')})")
    s = report.get("summary") or {}
    if s:
        lines.append(
            f"  name={s.get('name')!r} version={s.get('version')!r} "
            f"streaming={s.get('streaming')} skills={s.get('skills')} "
            f"interfaces={s.get('interfaces')}"
        )
    for c in report.get("checks", []):
        mark = "✓" if c["ok"] else "✗"
        lines.append(f"  [{mark}] {c['name']}: {c['detail']}")
    if report.get("errors"):
        lines.append("  errors: " + "; ".join(report["errors"]))
    if report.get("warnings"):
        lines.append("  warnings: " + "; ".join(report["warnings"]))
    return "\n".join(lines)


@mcp.tool()
async def a2a_validate(agent_card_url: str, bearer: str = "") -> str:
    """Validate ANY standard A2A endpoint's Agent Card and return a readable
    report (valid?/checks/warnings). `agent_card_url` is the full served card URL
    (e.g. `.../.well-known/agent-card.json`); `bearer` is optional (only needed
    if the card route requires auth). Nothing is connected or sent — this only
    fetches and inspects the card. The bearer is never echoed back."""
    report = await validate_agent_card(agent_card_url, bearer)
    return _format_report(report)


@mcp.tool()
async def a2a_connect(agent_card_url: str, bearer: str = "") -> str:
    """Connect to a standard A2A endpoint and start streaming its turns. Validates
    the Agent Card first and REFUSES to connect if it isn't usable (returns the
    validator report). On success, starts a background session and returns
    immediately — then call `a2a_wait_turn` for the first prompt. `bearer` is
    optional (pass it only if the endpoint requires auth). Neither the card URL's
    contents nor the bearer are echoed back."""
    report = await validate_agent_card(agent_card_url, bearer)
    if not report.get("valid"):
        return "Refusing to connect — the Agent Card did not validate:\n" + _format_report(report)

    await _teardown()
    global _session, _driver
    _session = A2ASession(agent_card_url, bearer)
    _driver = asyncio.create_task(_run_driver())
    return "Connected — call a2a_wait_turn for the first turn."


@mcp.tool()
async def a2a_wait_turn(max_seconds: int = 30) -> str:
    """Wait for the next turn's prompt, bounded by `max_seconds` (default 30) so
    this never hangs indefinitely. Returns the prompt text once it's your turn,
    "the session is over." once the remote agent finishes, an "Error: ..." string
    if the session broke, or "No turn yet — call a2a_wait_turn again." if the
    window elapsed first (just call it again). Show whatever this returns to your
    human and get their actual reply before calling a2a_send."""
    global _pending_prompt
    if _error:
        return f"Error: {_error}"
    if _done:
        return "The session is over."
    if _pending_prompt is not None:
        return _pending_prompt

    _prompt_event.clear()
    try:
        await asyncio.wait_for(_prompt_event.wait(), timeout=max_seconds)
    except asyncio.TimeoutError:
        return "No turn yet — call a2a_wait_turn again."

    if _error:
        return f"Error: {_error}"
    if _done:
        return "The session is over."
    if _pending_prompt is not None:
        return _pending_prompt
    return "No turn yet — call a2a_wait_turn again."


@mcp.tool()
async def a2a_send(message: str) -> str:
    """Submit your reply to the CURRENT pending turn (call a2a_wait_turn first to
    see the prompt). Returns immediately; call a2a_wait_turn again for the next
    turn. Rejected if it isn't your turn yet."""
    global _pending_prompt
    if _pending_prompt is None:
        return "It's not your turn — call a2a_wait_turn first."
    _pending_prompt = None
    _prompt_event.clear()
    _move_queue.put_nowait(message)
    return "Sent — call a2a_wait_turn for the next turn."


@mcp.tool()
async def a2a_status() -> str:
    """A quick snapshot: connected? a turn pending? session over? any error?
    Useful to re-orient after a gap in the conversation."""
    if _session is None:
        return "Not connected. Call a2a_connect (or a2a_validate first) to begin."
    if _error:
        return f"Error: {_error}"
    if _done:
        return "Connected. The session is over."
    if _pending_prompt is not None:
        return "Connected. Your turn is pending — call a2a_wait_turn to see it."
    return "Connected. Waiting for your turn — call a2a_wait_turn."


@mcp.tool()
async def a2a_leave() -> str:
    """Disconnect: cancel the background driver and close the session cleanly.
    Safe to call even if not connected."""
    await _teardown()
    return "Disconnected."


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
