"""Unit tests for `a2a_mcp_connector.a2a_client.A2ASession` — no network.

A fake transport (async `send_message`/`list_tasks`) is injected via the
`_transport_factory` seam; real a2a-sdk proto `Task`/`Message` objects are used
so `get_message_text` works exactly as in production.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from a2a.helpers import new_text_message
from a2a.types.a2a_pb2 import Role, Task, TaskState, TaskStatus

from a2a_mcp_connector.a2a_client import DONE, A2ASession

CARD_URL = "http://localhost:8791/api/a2a/agent/.well-known/agent-card.json"


def _task(state, text=None, tid="t1", cid="c1") -> Task:
    status = TaskStatus(state=state)
    if text is not None:
        status.message.CopyFrom(new_text_message(text, role=Role.ROLE_AGENT))
    return Task(id=tid, context_id=cid, status=status)


class _FakeTransport:
    """Scripted transport: `send_message` pops from `send_returns`; `list_tasks`
    returns `resume_task` (or none). Records the messages it was sent."""

    def __init__(self, send_returns: list[Task], resume_task: Task | None = None) -> None:
        self._send_returns = list(send_returns)
        self._resume_task = resume_task
        self.sent: list = []

    async def send_message(self, request):
        self.sent.append(request)
        return SimpleNamespace(task=self._send_returns.pop(0))

    async def list_tasks(self, request):
        tasks = [self._resume_task] if self._resume_task is not None else []
        return SimpleNamespace(tasks=tasks)


def test_absolute_url_required():
    with pytest.raises(ValueError):
        A2ASession("/relative/path", "tok")


def test_bearer_optional_no_auth_header_when_empty():
    s = A2ASession(CARD_URL, "")
    http = s._build_http_client()
    try:
        assert "authorization" not in {k.lower() for k in http.headers.keys()}
    finally:
        asyncio.run(http.aclose())


def test_bearer_sets_auth_header_when_present():
    s = A2ASession(CARD_URL, "sesame")
    http = s._build_http_client()
    try:
        assert http.headers.get("authorization") == "Bearer sesame"
    finally:
        asyncio.run(http.aclose())


def test_rpc_url_derived_from_card_path_not_advertised_interface():
    s = A2ASession(CARD_URL, "tok")
    assert s._rpc_url == "/api/a2a/agent"
    assert s._gateway == "http://localhost:8791"


def test_open_sends_hello_and_returns_first_prompt():
    transport = _FakeTransport(
        send_returns=[_task(TaskState.TASK_STATE_INPUT_REQUIRED, "your move?")]
    )
    s = A2ASession(CARD_URL, "tok", _transport_factory=lambda: transport)

    async def _body():
        prompt = await s.open()
        assert prompt == "your move?"
        # a fresh hello (no task_id) was sent
        assert len(transport.sent) == 1
        await s.close()

    asyncio.run(_body())


def test_open_resumes_an_existing_input_required_task_without_hello():
    resume = _task(TaskState.TASK_STATE_INPUT_REQUIRED, "resumed prompt", tid="rt", cid="rc")
    transport = _FakeTransport(send_returns=[], resume_task=resume)
    s = A2ASession(CARD_URL, "tok", _transport_factory=lambda: transport)

    async def _body():
        prompt = await s.open()
        assert prompt == "resumed prompt"
        # resumed → NO hello send_message
        assert transport.sent == []
        await s.close()

    asyncio.run(_body())


def test_reply_advances_to_next_prompt_then_done():
    transport = _FakeTransport(send_returns=[
        _task(TaskState.TASK_STATE_INPUT_REQUIRED, "turn 1"),
        _task(TaskState.TASK_STATE_INPUT_REQUIRED, "turn 2"),
        _task(TaskState.TASK_STATE_COMPLETED),
    ])
    s = A2ASession(CARD_URL, "tok", _transport_factory=lambda: transport)

    async def _body():
        assert await s.open() == "turn 1"
        assert await s.reply("a") == "turn 2"
        assert await s.reply("b") == DONE
        await s.close()

    asyncio.run(_body())


def test_reply_before_open_raises():
    s = A2ASession(CARD_URL, "tok", _transport_factory=lambda: _FakeTransport([]))

    async def _body():
        with pytest.raises(RuntimeError):
            await s.reply("x")

    asyncio.run(_body())


def test_terminal_task_on_open_returns_done():
    transport = _FakeTransport(send_returns=[_task(TaskState.TASK_STATE_COMPLETED)])
    s = A2ASession(CARD_URL, "tok", _transport_factory=lambda: transport)

    async def _body():
        assert await s.open() == DONE
        await s.close()

    asyncio.run(_body())
