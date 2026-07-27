"""Standard A2A Agent Card validator — the out-of-the-box validation this
connector ships, in the spirit of https://a2aprotocol.ai/a2a-protocol-validator.

`validate_agent_card()` fetches a remote Agent Card and reports whether it is a
usable standard-A2A endpoint. It is deliberately generic — it makes NO
assumption about which game/app served the card — so any A2A endpoint can be
validated through the same tool.

Two layers of checking:
  1. **SDK-compat (authoritative):** parse the raw JSON into the a2a-sdk
     `AgentCard` proto (`json_format.ParseDict(..., ignore_unknown_fields=True)`).
     If this fails, THIS connector's client (which uses the same SDK) cannot
     drive the endpoint — a fatal error.
  2. **Spec-structural:** required fields per the A2A Agent Card spec, tolerant
     of BOTH the current `supportedInterfaces` shape AND the older top-level
     `url`/`preferredTransport` (+ `additionalInterfaces`) shape, plus the
     `capabilities.streaming` capability.

Never raises on a bad endpoint — a network/parse failure becomes `valid=False`
with a reason. The bearer, if supplied, is sent as `Authorization: Bearer` and
is NEVER included in the returned report or any error text.
"""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

import httpx

from google.protobuf import json_format
from a2a.types.a2a_pb2 import AgentCard

# Cap the fetched card body so a hostile/huge endpoint can't blow up memory.
_MAX_CARD_BYTES = 256 * 1024
_FETCH_TIMEOUT = 15.0

# A fetch seam for tests: async (url, bearer) -> (status_code, content_type, text).
Fetcher = Callable[[str, str], Awaitable["tuple[int, str, str]"]]


async def _default_fetch(url: str, bearer: str) -> tuple[int, str, str]:
    headers = {"accept": "application/json"}
    if bearer:
        headers["authorization"] = f"Bearer {bearer}"
    timeout = httpx.Timeout(connect=10.0, read=_FETCH_TIMEOUT, write=10.0, pool=10.0)
    # follow_redirects=False (matches the RPC transport path): don't let a
    # server-controlled redirect bounce the card fetch to an internal address
    # (SSRF hardening) — an Agent Card is served directly, not behind a redirect.
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        async with client.stream("GET", url, headers=headers) as resp:
            ctype = resp.headers.get("content-type", "")
            # Enforce the cap DURING the read so a hostile/huge endpoint can't
            # exhaust memory before we slice — stop once we're one byte past it.
            buf = bytearray()
            async for chunk in resp.aiter_bytes():
                buf.extend(chunk)
                if len(buf) > _MAX_CARD_BYTES:
                    break
            body = bytes(buf[: _MAX_CARD_BYTES + 1]).decode("utf-8", "replace")
            return resp.status_code, ctype, body


def _get(card: dict, *names: str) -> Any:
    """First present value among `names` (camelCase and snake_case tolerated)."""
    for n in names:
        if n in card and card[n] not in (None, "", [], {}):
            return card[n]
    return None


def _interface_urls(card: dict) -> list[str]:
    """All declared interface URLs, across both card shapes."""
    urls: list[str] = []
    top = _get(card, "url")
    if isinstance(top, str):
        urls.append(top)
    for key in ("supportedInterfaces", "supported_interfaces",
                "additionalInterfaces", "additional_interfaces"):
        val = card.get(key)
        if isinstance(val, list):
            urls.extend(
                i["url"] for i in val
                if isinstance(i, dict) and isinstance(i.get("url"), str)
            )
    return urls


def _redact(exc: Exception, bearer: str) -> str:
    """Exception text with the bearer scrubbed (defensive — the bearer should
    never be in an httpx error anyway, but never risk echoing it)."""
    text = f"{type(exc).__name__}: {exc}"
    if bearer and bearer in text:
        text = text.replace(bearer, "***")
    return text


async def validate_agent_card(
    agent_card_url: str,
    bearer: str = "",
    *,
    _fetch: Fetcher | None = None,
) -> dict:
    """Validate a remote A2A Agent Card. Returns a structured report dict:
    `{valid, url, checks: [{name, ok, detail}], errors, warnings, summary}`.
    Never raises; the bearer never appears in the report."""
    checks: list[dict] = []
    errors: list[str] = []
    warnings: list[str] = []

    def add(name: str, ok: bool, detail: str, *, fatal: bool = False, warn: bool = False) -> bool:
        checks.append({"name": name, "ok": ok, "detail": detail})
        if not ok and fatal:
            errors.append(f"{name}: {detail}")
        if not ok and warn:
            warnings.append(f"{name}: {detail}")
        return ok

    fetch = _fetch or _default_fetch

    # --- fetch ---
    try:
        status, ctype, body = await fetch(agent_card_url, bearer)
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        add("reachable", False, f"could not fetch the Agent Card ({_redact(exc, bearer)})", fatal=True)
        return _report(agent_card_url, False, checks, errors, warnings, {})
    if status != 200:
        add("reachable", False, f"GET returned HTTP {status}", fatal=True)
        return _report(agent_card_url, False, checks, errors, warnings, {})
    if len(body.encode("utf-8", "ignore")) > _MAX_CARD_BYTES:
        add("size", False, f"card body exceeds {_MAX_CARD_BYTES} bytes", fatal=True)
        return _report(agent_card_url, False, checks, errors, warnings, {})
    add("reachable", True, f"HTTP 200 ({ctype or 'no content-type'})")

    # --- JSON parse ---
    try:
        card = json.loads(body)
        if not isinstance(card, dict):
            raise ValueError("top-level JSON is not an object")
    except Exception as exc:  # noqa: BLE001
        add("json", False, f"card is not valid JSON ({_redact(exc, bearer)})", fatal=True)
        return _report(agent_card_url, False, checks, errors, warnings, {})
    add("json", True, "card body is valid JSON")

    # --- SDK-compat (authoritative for whether this connector can drive it) ---
    try:
        json_format.ParseDict(card, AgentCard(), ignore_unknown_fields=True)
        sdk_ok = add("sdk_compatible", True, "parses into the a2a-sdk AgentCard model")
    except Exception as exc:  # noqa: BLE001
        sdk_ok = add(
            "sdk_compatible", False,
            f"does NOT parse into the a2a-sdk AgentCard model — this connector cannot drive it "
            f"({_redact(exc, bearer)})",
            fatal=True,
        )

    # --- spec-structural (tolerant of both card shapes) ---
    name = _get(card, "name")
    add("field.name", bool(name), "has a `name`" if name else "missing required `name`", fatal=True)
    version = _get(card, "version")
    add("field.version", bool(version),
        f"version = {version}" if version else "missing required `version`", fatal=True)
    caps = card.get("capabilities")
    add("field.capabilities", isinstance(caps, dict),
        "has a `capabilities` object" if isinstance(caps, dict) else "missing required `capabilities`",
        fatal=True)
    ifaces = _interface_urls(card)
    add("field.interface", bool(ifaces),
        f"{len(ifaces)} interface URL(s) declared" if ifaces
        else "no interface URL (`url`/`supportedInterfaces`/`additionalInterfaces`)",
        fatal=True)

    # non-fatal spec gaps
    add("field.description", bool(_get(card, "description")),
        "has a `description`" if _get(card, "description") else "missing recommended `description`",
        warn=True)
    modes_in = _get(card, "defaultInputModes", "default_input_modes")
    modes_out = _get(card, "defaultOutputModes", "default_output_modes")
    add("field.io_modes", bool(modes_in) and bool(modes_out),
        "declares default input+output modes" if (modes_in and modes_out)
        else "missing `defaultInputModes`/`defaultOutputModes`", warn=True)
    skills = card.get("skills")
    add("field.skills", isinstance(skills, list) and len(skills) > 0,
        f"{len(skills)} skill(s)" if isinstance(skills, list) and skills
        else "no `skills` declared", warn=True)

    # --- streaming capability (informational: this connector uses message/send,
    #     which does not require streaming, but message/stream clients do) ---
    streaming = bool(isinstance(caps, dict) and caps.get("streaming"))
    add("capability.streaming", streaming,
        "supports message/stream (capabilities.streaming = true)" if streaming
        else "does not advertise streaming (capabilities.streaming falsy) — "
             "fine for message/send, no streaming for message/stream",
        warn=True)

    valid = sdk_ok and bool(name) and bool(version) and isinstance(caps, dict) and bool(ifaces)
    summary = {
        "name": name,
        "version": version,
        "streaming": streaming,
        "interfaces": ifaces,
        "skills": len(skills) if isinstance(skills, list) else 0,
    }
    return _report(agent_card_url, valid, checks, errors, warnings, summary)


def _report(url: str, valid: bool, checks: list, errors: list, warnings: list, summary: dict) -> dict:
    return {
        "valid": valid,
        "url": url,
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
        "summary": summary,
    }
