# Conventions

- Use typed async Python adapters; `GameState` methods build validated Lua requests and return narrated/action results.
- Arena tools are discrete `ToolDef` entries in `src/civ_mcp/arena/registry.py`; action tools declare a non-empty analysis `verb`.
- `src/civ_mcp/arena/vocab.py:LOCAL_TOOL_VERBS` must exactly mirror registry action verbs; tests enforce parity.
- Arena `full` is derived from `tuple(TOOL_REGISTRY)`; smaller tiers are explicit ordered tuples and order is model-visible.
- Keep changes scoped; new arena adapters should wrap existing `GameState` behavior unless game-side work is explicitly required.
- Prefer exact snapshot/behavior assertions for experiment isolation and tool tiers.
- Tracked live-operation docs live under `tools/skills/`; `.claude/` copies are local/ignored.