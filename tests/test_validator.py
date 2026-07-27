"""Unit tests for `a2a_mcp_connector.validator.validate_agent_card` — no network.

A fake fetcher `(url, bearer) -> (status, content_type, body)` is injected via
the `_fetch` seam.
"""
from __future__ import annotations

import asyncio
import json

from a2a_mcp_connector.validator import validate_agent_card

URL = "https://host/api/a2a/agent/.well-known/agent-card.json"

VALID_CARD = {
    "name": "Test Agent",
    "description": "a test agent",
    "version": "1.0.0",
    "protocolVersion": "1.0",
    "capabilities": {"streaming": True},
    "defaultInputModes": ["text/plain"],
    "defaultOutputModes": ["text/plain"],
    "skills": [{"id": "s1", "name": "chat", "description": "d", "tags": ["t"]}],
    "supportedInterfaces": [{"url": "https://host/api/a2a/agent", "protocolBinding": "JSONRPC"}],
}


def _fetch_ok(card: dict, status: int = 200, ctype: str = "application/json"):
    async def _f(url: str, bearer: str):
        return status, ctype, json.dumps(card)
    return _f


def _run(coro):
    return asyncio.run(coro)


def test_valid_card_passes_all_fatal_checks():
    report = _run(validate_agent_card(URL, "", _fetch=_fetch_ok(VALID_CARD)))
    assert report["valid"] is True
    assert report["errors"] == []
    assert report["summary"]["name"] == "Test Agent"
    assert report["summary"]["streaming"] is True
    assert report["summary"]["interfaces"] == ["https://host/api/a2a/agent"]
    by = {c["name"]: c["ok"] for c in report["checks"]}
    assert by["sdk_compatible"] and by["field.name"] and by["field.interface"]


def test_unreachable_endpoint_is_invalid():
    async def _boom(url: str, bearer: str):
        raise ConnectionError("refused")
    report = _run(validate_agent_card(URL, "", _fetch=_boom))
    assert report["valid"] is False
    assert any("reachable" in e for e in report["errors"])


def test_non_200_is_invalid():
    report = _run(validate_agent_card(URL, "", _fetch=_fetch_ok(VALID_CARD, status=404)))
    assert report["valid"] is False
    assert any("HTTP 404" in c["detail"] for c in report["checks"])


def test_non_json_body_is_invalid():
    async def _f(url: str, bearer: str):
        return 200, "text/html", "<html>not json</html>"
    report = _run(validate_agent_card(URL, "", _fetch=_f))
    assert report["valid"] is False
    assert any(c["name"] == "json" and not c["ok"] for c in report["checks"])


def test_sdk_incompatible_card_is_invalid():
    bad = dict(VALID_CARD)
    bad["skills"] = "not-a-list"  # repeated field given a string -> proto ParseDict error
    report = _run(validate_agent_card(URL, "", _fetch=_fetch_ok(bad)))
    assert report["valid"] is False
    assert any(c["name"] == "sdk_compatible" and not c["ok"] for c in report["checks"])


def test_missing_name_is_invalid():
    bad = dict(VALID_CARD)
    del bad["name"]
    report = _run(validate_agent_card(URL, "", _fetch=_fetch_ok(bad)))
    assert report["valid"] is False
    assert any(c["name"] == "field.name" and not c["ok"] for c in report["checks"])


def test_no_streaming_is_valid_but_warns():
    card = dict(VALID_CARD)
    card["capabilities"] = {"streaming": False}
    report = _run(validate_agent_card(URL, "", _fetch=_fetch_ok(card)))
    assert report["valid"] is True  # message/send works without streaming
    assert report["summary"]["streaming"] is False
    assert any(c["name"] == "capability.streaming" and not c["ok"] for c in report["checks"])
    assert any("streaming" in w for w in report["warnings"])


def test_legacy_top_level_url_shape_counts_as_an_interface():
    card = dict(VALID_CARD)
    del card["supportedInterfaces"]
    card["url"] = "https://host/api/a2a/agent"  # 0.x top-level url shape
    card["preferredTransport"] = "JSONRPC"
    report = _run(validate_agent_card(URL, "", _fetch=_fetch_ok(card)))
    assert report["summary"]["interfaces"] == ["https://host/api/a2a/agent"]
    assert any(c["name"] == "field.interface" and c["ok"] for c in report["checks"])


def test_bearer_never_appears_in_report_even_when_fetch_error_contains_it():
    async def _boom(url: str, bearer: str):
        raise ConnectionError(f"failed talking to {bearer}@host")
    report = _run(validate_agent_card(URL, "topsecret", _fetch=_boom))
    assert "topsecret" not in json.dumps(report)
