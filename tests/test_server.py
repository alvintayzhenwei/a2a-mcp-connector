"""Tests for the FastMCP tool surface in `a2a_mcp_connector.server` — no network.

Tools are plain async functions (`@mcp.tool()` returns the same callable), so
they're called directly. `server.A2ASession` and `server.validate_agent_card`
are monkeypatched so nothing touches the network.
"""
from __future__ import annotations

import asyncio
import time

import pytest

import a2a_mcp_connector.server as server

CARD_URL = "http://localhost:8791/api/a2a/agent/.well-known/agent-card.json"
BEARER = "super-secret-bearer"


class _FakeSession:
    def __init__(self, agent_card_url, bearer, *, prompts=None,
                 open_error=None, hang_on_open=False):
        self.agent_card_url = agent_card_url
        self.bearer = bearer
        self._prompts = list(prompts or [])
        self._open_error = open_error
        self._hang_on_open = hang_on_open
        self.closed = False

    async def open(self):
        if self._hang_on_open:
            await asyncio.sleep(3600)
        if self._open_error is not None:
            raise self._open_error
        return self._prompts.pop(0)

    async def reply(self, move):
        return self._prompts.pop(0)

    async def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _reset_state():
    server._reset_state()
    yield
    server._reset_state()


@pytest.fixture(autouse=True)
def _valid_by_default(monkeypatch):
    """a2a_connect validates first; default that to VALID so connect proceeds."""
    async def _ok(url, bearer=""):
        return {"valid": True, "url": url, "checks": [], "errors": [], "warnings": [], "summary": {}}
    monkeypatch.setattr(server, "validate_agent_card", _ok)


def _install(monkeypatch, **kwargs) -> _FakeSession:
    fake = _FakeSession(CARD_URL, BEARER, **kwargs)

    def _factory(agent_card_url, bearer=""):
        assert agent_card_url == CARD_URL
        assert bearer == BEARER
        return fake

    monkeypatch.setattr(server, "A2ASession", _factory)
    return fake


def test_validate_tool_formats_report(monkeypatch):
    async def _rep(url, bearer=""):
        return {"valid": True, "url": url, "checks": [{"name": "reachable", "ok": True, "detail": "HTTP 200"}],
                "errors": [], "warnings": ["capability.streaming: none"], "summary": {"name": "X", "version": "1"}}
    monkeypatch.setattr(server, "validate_agent_card", _rep)

    async def _body():
        out = await server.a2a_validate(CARD_URL)
        assert "VALID" in out and "reachable" in out and "capability.streaming" in out
    asyncio.run(_body())


def test_connect_refuses_when_card_invalid(monkeypatch):
    async def _bad(url, bearer=""):
        return {"valid": False, "url": url, "checks": [], "errors": ["field.name: missing"],
                "warnings": [], "summary": {}}
    monkeypatch.setattr(server, "validate_agent_card", _bad)
    _install(monkeypatch, prompts=["should not be reached"])

    async def _body():
        out = await server.a2a_connect(CARD_URL, BEARER)
        assert "Refusing to connect" in out
        assert server._session is None  # never started the driver
    asyncio.run(_body())


def test_connect_then_wait_returns_first_prompt(monkeypatch):
    _install(monkeypatch, prompts=["your move?"])

    async def _body():
        assert "Connected" in await server.a2a_connect(CARD_URL, BEARER)
        assert await server.a2a_wait_turn(max_seconds=5) == "your move?"
        await server.a2a_leave()
    asyncio.run(_body())


def test_send_advances_to_next_prompt(monkeypatch):
    _install(monkeypatch, prompts=["first", "second"])

    async def _body():
        await server.a2a_connect(CARD_URL, BEARER)
        assert await server.a2a_wait_turn(max_seconds=5) == "first"
        assert "Sent" in await server.a2a_send("hi")
        assert await server.a2a_wait_turn(max_seconds=5) == "second"
        await server.a2a_leave()
    asyncio.run(_body())


def test_send_before_a_pending_turn_is_rejected(monkeypatch):
    _install(monkeypatch, hang_on_open=True)

    async def _body():
        await server.a2a_connect(CARD_URL, BEARER)
        assert "not your turn" in await server.a2a_send("x")
        await server.a2a_leave()
    asyncio.run(_body())


def test_wait_turn_is_bounded(monkeypatch):
    _install(monkeypatch, hang_on_open=True)

    async def _body():
        await server.a2a_connect(CARD_URL, BEARER)
        start = time.monotonic()
        out = await server.a2a_wait_turn(max_seconds=1)
        assert "No turn yet" in out
        assert time.monotonic() - start < 2.5
        await server.a2a_leave()
    asyncio.run(_body())


def test_error_on_open_surfaces_via_wait_and_status(monkeypatch):
    _install(monkeypatch, open_error=RuntimeError("gateway down"))

    async def _body():
        await server.a2a_connect(CARD_URL, BEARER)
        assert "gateway down" in await server.a2a_wait_turn(max_seconds=5)
        assert "gateway down" in await server.a2a_status()
        await server.a2a_leave()
    asyncio.run(_body())


def test_done_after_terminal(monkeypatch):
    _install(monkeypatch, prompts=[server.DONE])

    async def _body():
        await server.a2a_connect(CARD_URL, BEARER)
        assert "over" in await server.a2a_wait_turn(max_seconds=5)
        await server.a2a_leave()
    asyncio.run(_body())


def test_status_and_ops_before_connect_are_safe():
    async def _body():
        assert "Not connected" in await server.a2a_status()
        assert "No turn yet" in await server.a2a_wait_turn(max_seconds=1)
        assert "not your turn" in await server.a2a_send("x")
        assert "Disconnected" in await server.a2a_leave()
    asyncio.run(_body())


def test_reconnect_without_leave_closes_old_session(monkeypatch):
    first = _install(monkeypatch, prompts=["first session"])

    async def _body():
        await server.a2a_connect(CARD_URL, BEARER)
        await server.a2a_wait_turn(max_seconds=5)
        assert first.closed is False
        second = _FakeSession(CARD_URL, BEARER, prompts=["second session"])
        monkeypatch.setattr(server, "A2ASession", lambda u, b="": second)
        await server.a2a_connect(CARD_URL, BEARER)
        assert first.closed is True
        assert server._session is second
        await server.a2a_leave()
    asyncio.run(_body())


def test_leave_cancels_hung_driver_cleanly(monkeypatch):
    fake = _install(monkeypatch, hang_on_open=True)

    async def _body():
        await server.a2a_connect(CARD_URL, BEARER)
        assert "Disconnected" in await server.a2a_leave()
        assert fake.closed is True
        assert "Not connected" in await server.a2a_status()
    asyncio.run(_body())


def test_bearer_never_in_any_output(monkeypatch):
    _install(monkeypatch, prompts=["turn 1", "turn 2"])

    async def _body():
        outs = [
            await server.a2a_connect(CARD_URL, BEARER),
            await server.a2a_wait_turn(max_seconds=5),
            await server.a2a_send("x"),
            await server.a2a_wait_turn(max_seconds=5),
            await server.a2a_status(),
            await server.a2a_leave(),
        ]
        for o in outs:
            assert BEARER not in o
    asyncio.run(_body())
