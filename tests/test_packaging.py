"""The published dependency metadata has to match the API this code actually uses.

Regression guard. `server.py` imports `mcp.server.fastmcp.FastMCP`, which mcp 2.0
renamed to `mcp.server.mcpserver.MCPServer` and deleted. The manifest declared
`mcp>=1.12` with no upper bound, so `uvx a2a-mcp-connector` resolved mcp 2.1.1 and the
server died at import with

    ModuleNotFoundError: No module named 'mcp.server.fastmcp'

before registering a single tool — which is every visitor who followed the connect
panel's own copy-paste install line. Nothing in the test suite noticed, because the
suite runs against a locked dev environment that already had mcp 1.x; only a FRESH
resolve picks the new major. So the thing worth testing is the METADATA, not the
import.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
SERVER = Path(__file__).resolve().parents[1] / "src" / "a2a_mcp_connector" / "server.py"


def _requirement(name: str) -> str:
    deps = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["dependencies"]
    for dep in deps:
        if dep.split(">=")[0].split("==")[0].split("<")[0].strip() == name:
            return dep
    raise AssertionError(f"{name} is not a declared dependency")


def test_mcp_is_capped_below_the_next_major() -> None:
    # A floor alone is not enough: the failure was an unbounded ceiling, and any
    # future `mcp` major can rename the API out from under us the same way.
    assert "<2" in _requirement("mcp"), (
        "mcp must stay capped below 2 while server.py imports mcp.server.fastmcp — "
        "see the note in pyproject.toml (and the longer one in a2a-raid-mcp). If you migrate to MCPServer, move the "
        "FLOOR to >=2 rather than removing the cap."
    )


def test_the_cap_matches_the_import_the_server_actually_uses() -> None:
    # Ties the pin to the reason for it, so migrating the code and forgetting the
    # manifest (or vice versa) fails here rather than in a stranger's terminal.
    source = SERVER.read_text(encoding="utf-8")
    uses_v1_api = "mcp.server.fastmcp" in source
    requirement = _requirement("mcp")
    if uses_v1_api:
        assert "<2" in requirement, "v1 import path needs the <2 cap"
    else:
        assert ">=2" in requirement, (
            "server.py no longer imports the v1 path, so the manifest should ask "
            "for mcp>=2 rather than capping below it"
        )


def test_every_dependency_states_a_bound() -> None:
    # The narrower lesson: this ecosystem breaks across majors, so an unbounded
    # requirement is a latent outage, not a convenience.
    deps = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["dependencies"]
    for dep in deps:
        assert any(op in dep for op in ("==", ">=", "~=")), f"{dep} states no version bound"
