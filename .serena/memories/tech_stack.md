# Tech stack

- Python >=3.12; package/build metadata in `pyproject.toml`; Hatchling build backend.
- Dependency/environment manager: `uv`.
- MCP SDK >=1.20; FastAPI >=0.128; Anthropic/OpenAI clients; PyYAML; HTTPX.
- Tests: pytest + pytest-asyncio with `asyncio_mode = auto`.
- Console entrypoints: `civ-mcp`, `civ-arena`, `civ-arena-analyze`.
- Source layout package: `src/civ_mcp`.