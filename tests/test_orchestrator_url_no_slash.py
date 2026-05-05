"""
Sentinel test: the orchestrator must connect to "/mcp" without a trailing slash.

Why this exists: FastMCP serves streamable-http at "/mcp" (no slash). If the
URL the MCP client uses has a trailing slash ("/mcp/"), FastMCP responds with
HTTP 307 to "/mcp", and the httpx-based streamable_http_client breaks on the
redirect. The failure is opaque — a TaskGroup exception with no hint that a
redirect was the issue. We hit this in May 2026 and burned a debugging
session on it.

The fix is the trivial-looking absence of a trailing slash in the URL string
in scitoolkit/serve/orchestrator.py. This test asserts that absence so that a
future agent (or autoformatter, or a "consistency" pass) cannot silently
re-add the slash without a failing test calling it out.

If FastMCP's default path changes from "/mcp" to something else, update the
URL in orchestrator.py AND update this test together.
"""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
ORCHESTRATOR_PATH = REPO_ROOT / "scitoolkit" / "serve" / "orchestrator.py"


def test_orchestrator_uses_mcp_url_without_trailing_slash():
    """The MCPClient URL in orchestrator.py must end with '/mcp', not '/mcp/'."""
    src = ORCHESTRATOR_PATH.read_text()

    # The exact URL site we care about. We're matching the f-string literal
    # so a refactor that wraps the URL in a helper function still has to
    # update this test alongside the new construction site.
    good = 'url=f"http://127.0.0.1:{port}/mcp"'
    bad = 'url=f"http://127.0.0.1:{port}/mcp/"'

    assert good in src, (
        f"Expected the canonical MCPClient URL `{good}` in "
        f"{ORCHESTRATOR_PATH}. If you renamed the variable, refactored the "
        f"URL into a helper, or changed FastMCP's path, update this test."
    )
    assert bad not in src, (
        f"Found a trailing-slash MCP URL (`{bad}`) in {ORCHESTRATOR_PATH}. "
        f"FastMCP's streamable-http endpoint is at /mcp (no slash); a "
        f"trailing slash causes a 307 redirect that breaks the MCP session "
        f"silently. Drop the slash. See the comment block at the URL site "
        f"in orchestrator.py for context."
    )


def test_orchestrator_url_appears_exactly_once():
    """Defense-in-depth: don't let the URL silently fork into two places."""
    src = ORCHESTRATOR_PATH.read_text()
    occurrences = src.count("/mcp")
    # We expect: 1 in the URL itself, plus references in the warning comment
    # block. The point of this test is to catch someone copying the URL line
    # to a second site without porting the comment. So we check the *URL
    # construction string* specifically, not bare "/mcp" mentions.
    url_construction_count = src.count('http://127.0.0.1:{port}/mcp')
    assert url_construction_count == 1, (
        f"Expected exactly 1 MCPClient URL construction site, found "
        f"{url_construction_count}. If you have a real reason to construct "
        f"this URL in two places (e.g. a fallback transport), update this "
        f"test — but read the warning block in orchestrator.py first."
    )
