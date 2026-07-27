"""Self-contained, generic standard-A2A client for ONE remote A2A endpoint.

This is intentionally standalone: the package is publishable to PyPI (see
`a2a-mcp-connector/README.md`), so it must NOT import `a2a_games` (an
unpublished sibling) — and it deliberately does NOT import `a2a_raid_mcp`
either, even though `a2a_raid_mcp.a2a_client.SeatSession` is the same shape.
The two are kept independently readable rather than sharing an internal dep
between separate distributions. This one is the GENERIC version: no
game/raid-specific `/chat` party endpoint, and the bearer is OPTIONAL (a public
A2A endpoint may be unauthenticated).

Protocol shape (standard A2A `input-required` multi-turn, same as the raid
client): one A2A task maps to the whole interaction. The first `message/send`
(no task_id) returns a `Task` in `TASK_STATE_INPUT_REQUIRED` carrying the
current turn's prompt; each following `message/send` (same task_id/context_id)
submits that turn's reply and returns either the NEXT `INPUT_REQUIRED` prompt
or a terminal state (`COMPLETED`/`FAILED`/`CANCELED`/`REJECTED`) once the
remote agent is done. The bearer, when supplied, is stored only in memory and
is never logged, printed, or included in any exception message.
"""
from __future__ import annotations

from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

from a2a.client.card_resolver import A2ACardResolver
from a2a.client.transports.jsonrpc import JsonRpcTransport
from a2a.helpers import get_message_text, new_text_message
from a2a.types.a2a_pb2 import ListTasksRequest, Role, SendMessageRequest, Task, TaskState
from a2a.utils.constants import PROTOCOL_VERSION_1_0, VERSION_HEADER

# Sentinel returned by `open()`/`reply()` once the remote task has reached a
# terminal state (no further turns are expected).
DONE = "__DONE__"

# Held-request read timeout: a well-behaved A2A server may hold a reply's
# `message/send` open across a pause before returning the next turn's prompt.
# A pragmatic ceiling — not an attempt to ride out an arbitrarily long wait.
_TURN_READ_TIMEOUT = 200.0

TransportFactory = Callable[[], Any]


class A2ASession:
    """One remote A2A endpoint's standard-A2A session: resolve its Agent Card,
    open (or resume) a task, and drive turns via `reply()`.

    `agent_card_url` is the FULL served card URL (e.g.
    `https://host/api/a2a/agent/.well-known/agent-card.json`). It is split into
    the origin (scheme + host[:port]) and the card's own path.

    `bearer` is OPTIONAL — pass `""`/`None` for an unauthenticated public
    endpoint; when set it is sent as `Authorization: Bearer <bearer>` and never
    logged.

    `_transport_factory` is a PRIVATE test seam: a zero-argument callable that,
    if provided, builds the transport in place of the real `A2ACardResolver` +
    `JsonRpcTransport` pair, so tests inject a fake transport with async
    `send_message`/`list_tasks` and touch no network. Never used outside tests.
    """

    def __init__(
        self,
        agent_card_url: str,
        bearer: str | None = None,
        *,
        _transport_factory: TransportFactory | None = None,
    ) -> None:
        parts = urlsplit(agent_card_url)
        if not parts.scheme or not parts.netloc:
            raise ValueError(f"agent_card_url must be an absolute URL, got: {agent_card_url!r}")
        self._gateway = f"{parts.scheme}://{parts.netloc}"
        self._card_path = parts.path
        # The RPC endpoint is the card's own path with the well-known suffix
        # stripped — NOT the card's advertised interface URL, which may not be
        # reachable behind a reverse proxy (the dev proxy doesn't rewrite it).
        self._rpc_url = self._card_path.rsplit("/.well-known/", 1)[0] or "/"
        self._bearer = bearer or ""
        self._transport_factory = _transport_factory

        self._http: httpx.AsyncClient | None = None
        self._transport: Any = None
        self._task: Task | None = None

    def _build_http_client(self) -> httpx.AsyncClient:
        headers = {
            # A transport built directly (not via the SDK's ClientFactory) does
            # not set the protocol version for us; a missing A2A-Version header
            # reads as the legacy protocol server-side.
            VERSION_HEADER: PROTOCOL_VERSION_1_0,
        }
        if self._bearer:
            headers["authorization"] = f"Bearer {self._bearer}"
        timeout = httpx.Timeout(connect=10.0, read=_TURN_READ_TIMEOUT, write=10.0, pool=10.0)
        return httpx.AsyncClient(base_url=self._gateway, headers=headers, timeout=timeout)

    async def open(self) -> str:
        """Resolve the Agent Card, resume an open `input-required` task if one
        exists (else send a fresh `hello`), and return the current turn's prompt
        text — or `DONE` if the task is already terminal."""
        self._http = self._build_http_client()
        if self._transport_factory is not None:
            self._transport = self._transport_factory()
        else:
            resolver = A2ACardResolver(self._http, self._gateway)
            card = await resolver.get_agent_card(relative_card_path=self._card_path)
            self._transport = JsonRpcTransport(self._http, card, self._rpc_url)

        task = await self._resume_open_task()
        if task is None:
            response = await self._transport.send_message(
                SendMessageRequest(message=new_text_message("hello", role=Role.ROLE_USER))
            )
            task = response.task
        self._task = task
        return self._prompt_or_done(task)

    async def reply(self, text: str) -> str:
        """Submit `text` as the reply to the current turn and return the NEXT
        turn's prompt, or `DONE` once the task reaches a terminal state. Must be
        called after `open()`."""
        if self._task is None:
            raise RuntimeError("A2ASession.reply() called before open()")
        response = await self._transport.send_message(
            SendMessageRequest(message=new_text_message(
                text, role=Role.ROLE_USER,
                task_id=self._task.id, context_id=self._task.context_id,
            ))
        )
        self._task = response.task
        return self._prompt_or_done(self._task)

    async def close(self) -> None:
        """Close the underlying httpx client."""
        if self._http is not None:
            await self._http.aclose()

    async def _resume_open_task(self) -> Task | None:
        """List this connection's own tasks and return one still awaiting a
        reply (`TASK_STATE_INPUT_REQUIRED`), or `None`. Any transport error
        (e.g. a server without `ListTasks`) is treated as "nothing to resume"
        so a fresh `hello` still works."""
        try:
            response = await self._transport.list_tasks(
                ListTasksRequest(status=TaskState.TASK_STATE_INPUT_REQUIRED)
            )
        except Exception:
            return None
        return response.tasks[0] if response.tasks else None

    @staticmethod
    def _prompt_or_done(task: Task | None) -> str:
        if task is None or task.status.state != TaskState.TASK_STATE_INPUT_REQUIRED:
            return DONE
        if not task.status.message:
            return ""
        return get_message_text(task.status.message)
