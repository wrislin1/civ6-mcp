# civ6-mcp core

- MCP server bridges LLM clients to a live Civilization VI game through FireTuner TCP port 4318; game APIs enforce rules.
- Primary package: `src/civ_mcp`; live MCP surface: `src/civ_mcp/server.py`; state/Lua adapter: `src/civ_mcp/game_state.py` and `src/civ_mcp/lua/`; local-agent arena: `src/civ_mcp/arena/`.
- Tests mirror package structure under `tests/`, with the large arena suite under `tests/arena/`.
- Operational live-arena skill and scripts are tracked under `tools/skills/civ6-arena-live/`; `.claude/` is a gitignored local mirror.
- Read `mem:tech_stack` for runtime/build pins, `mem:conventions` for local patterns, `mem:suggested_commands` for entrypoints, and `mem:task_completion` for verification gates.