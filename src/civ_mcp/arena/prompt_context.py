from __future__ import annotations

from typing import Any, Collection

from civ_mcp.narrate import ToolSurface
from civ_mcp.arena.briefing import Briefing, build_briefing
from civ_mcp.arena.budget import briefing_budget


async def maybe_build_briefing(
    gs: Any,
    options: Any,
    *,
    n_ctx: int,
    playbook_chars: int,
    tool_schema_chars: int,
    supplied: Briefing | None = None,
    surface: ToolSurface,
    available_tools: Collection[str] | None = None,
) -> Briefing:
    """Render the opening briefing for one seat.

    ``surface`` is required: a default would silently hand an arena seat MCP
    call syntax for tools that are not in its schema, with no exception and no
    test failure.
    """
    if supplied is not None:
        return supplied
    if not options.briefing.enabled:
        return Briefing()
    budget = briefing_budget(
        n_ctx,
        options,
        playbook_chars,
        tool_schema_chars,
    )
    return await build_briefing(
        gs,
        options.briefing,
        budget,
        surface=surface,
        available_tools=available_tools,
    )
