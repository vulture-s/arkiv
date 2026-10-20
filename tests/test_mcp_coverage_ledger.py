"""Every HTTP route is accounted for in tests/mcp_coverage_ledger.py.

Without this, a new endpoint ships and nobody decides whether an AI client
should see it: the REST and MCP surfaces drift apart and the gap is only found
when an agent can't answer something the UI can. The ledger turns that into a
red test at review time. See the ledger's docstring for the categories.
"""
import pytest

# mcp_server imports the MCP SDK, which is 3.10+ and gated out on 3.9
# (same reason as tests/test_mcp_server.py).
pytest.importorskip("mcp")

import mcp_server
import server
from tests.mcp_coverage_ledger import CATEGORIES, MCP_EQUIVALENTS, NOT_EXPOSED


def _routes():
    paths = server.app.openapi()["paths"]
    return {f"{method.upper()} {path}" for path, ops in paths.items() for method in ops}


def test_every_route_is_in_the_ledger():
    missing = sorted(_routes() - MCP_EQUIVALENTS.keys() - NOT_EXPOSED.keys())
    assert not missing, (
        "New route(s) with no MCP decision. Add each to tests/mcp_coverage_ledger.py: "
        "MCP_EQUIVALENTS if an MCP tool covers it, otherwise NOT_EXPOSED with a "
        f"category and reason: {missing}"
    )


def test_no_route_is_in_both_tables():
    both = sorted(MCP_EQUIVALENTS.keys() & NOT_EXPOSED.keys())
    assert not both, f"Route(s) both covered and not exposed: {both}"


def test_ledger_names_only_routes_that_exist():
    stale = sorted((MCP_EQUIVALENTS.keys() | NOT_EXPOSED.keys()) - _routes())
    assert not stale, f"Ledger entries for routes that no longer exist: {stale}"


async def test_equivalents_name_real_mcp_tools():
    tools = {t.name for t in await mcp_server.mcp.list_tools()}
    named = {name for names, _ in MCP_EQUIVALENTS.values() for name in names}
    assert named - tools == set(), f"Ledger names unknown MCP tools: {sorted(named - tools)}"
    # The other direction: a tool that maps to no route is either a new
    # capability with no REST twin (fine, but say so here) or a typo above.
    assert tools - named == set(), f"MCP tools with no ledger route: {sorted(tools - named)}"


def test_every_exclusion_has_a_known_category_and_a_reason():
    for route, (category, reason) in NOT_EXPOSED.items():
        assert category in CATEGORIES, f"{route}: unknown category {category!r}"
        assert reason.strip(), f"{route}: empty reason"
