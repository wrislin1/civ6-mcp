# Suggested commands

- Install/sync: `uv sync --extra test`.
- Full tests: `uv run --extra test pytest -q`.
- Arena tests: `uv run --extra test pytest tests/arena -q`.
- Focused test: `uv run --extra test pytest path/to/test.py::test_name -v`.
- Run MCP server: `uv run civ-mcp`.
- Run arena/analyzer: `uv run civ-arena`; `uv run civ-arena-analyze`.
- Live connection probe with Civ running: `uv run python scripts/test_connection.py`.
- Whitespace check before commit: `git diff --check`.