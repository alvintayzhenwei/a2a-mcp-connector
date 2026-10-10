# Security policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately, **not** as a public issue.

- Preferred: open a [private security advisory](https://github.com/alvintayzhenwei/a2a-mcp-connector/security/advisories/new)
  on this repository.
- Alternative: email <alvintay1987@gmail.com> with `a2a-mcp-connector` in the subject.

Expect an acknowledgement within 7 days. If a fix is warranted, it ships as a
new release on [PyPI](https://pypi.org/project/a2a-mcp-connector/) and the advisory is
published once users have had a chance to upgrade.

## Supported versions

Only the latest released version is supported. This package is small and has no
long-term support branches; fixes land on `main` and are released from there.

## What this package does with your data

`a2a-mcp-connector` is an MCP **client**. It holds the endpoint URL and the optional
bearer token you hand it in memory for the life of the session, sends the token
only as an `Authorization` header to the endpoint you named, and never writes
either to disk, to a log line, or into any tool's return value. It has no
telemetry and contacts no host other than the one you point it at.

## How this repository is checked

Every push and pull request runs:

- **CI** — the test suite on Python 3.11 and 3.12, against the exact locked
  dependency set (`uv sync --frozen`).
- **pip-audit** — the locked runtime dependencies checked against the Python
  advisory database. Also runs weekly, so a CVE published against a pinned
  dependency surfaces even when nobody has pushed.
- **CodeQL** — static analysis (`security-and-quality`) with results in this
  repository's Security tab.
- **Dependabot** — weekly dependency and GitHub Actions update pull requests.

## The dependency pins are deliberate

Two pins in `pyproject.toml` look like neglect and are not. Please do not
"modernise" them in a drive-by pull request:

- **`mcp>=1.12,<2`** — `server.py` imports `mcp.server.fastmcp.FastMCP`. mcp 2.0
  renamed that to `mcp.server.mcpserver.MCPServer` and deleted the old path, so
  under 2.x the server dies at import and registers no tools. With an open upper
  bound this shipped to real users. Migrating is welcome, but it is a code
  change with its own verification pass — move the floor to `>=2`, don't widen
  the range. `tests/test_packaging.py` asserts the cap and ties it to the import
  the server actually uses, so the manifest and the code cannot drift apart
  silently.
- **`a2a-sdk==1.1.5`** — the A2A SDK has real API churn between minor versions.
  The call shapes in `a2a_client.py` are verified against exactly this version.

Dependabot blocks MCP 2.x and A2A SDK minor/major upgrades (see
`.github/dependabot.yml`); patch updates remain eligible for review.
`pip-audit` is not: if either pinned version is ever the subject of an advisory,
the audit job fails and the pin gets revisited deliberately.

Security floors for transitive dependencies are declared in `pyproject.toml`
and enforced in the published wheel metadata: `pyjwt>=2.15.0,<3` and
`urllib3>=2.8.0,<3`. Updating `uv.lock` alone would not protect fresh installs
of the published package. Packaging tests check the floors, locked versions,
and installed versions.
