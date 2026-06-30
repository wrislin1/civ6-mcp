# Arena live-gate finding: headless CLI civ loads no civ6 MCP tools

**Status:** OPEN — blocks the `arena-vertical-slice` CLI-civ driver from functioning.
**Found:** live gate on the gaming PC (`riz@192.168.20.141`, WSL2), branch `arena-vertical-slice` @ `19cfb81`.
**Severity:** High — the CLI-civ driver is non-functional for actual play (it has no tools to call).

## Symptom

When the arena drives a civ via a headless `claude -p` subprocess (`src/civ_mcp/arena/cli_agent.py`),
the agent receives **zero civ6 MCP tools**. In the real arena run the CLI civ floundered
(`"[Calling get_game_overview] … (no tool — let me actually call)"`), made no tool calls,
and returned a mid-thought summary. The turn was still seized and handed back correctly, and
cost was logged ($0.14) — only the *gameplay* was empty.

## What was tested (all → `NO_CIV6_TOOLS`)

A headless `claude -p` told to call `get_game_overview` returns "no such tool" under every variant:

| Variant | Result |
|---|---|
| `--mcp-config .mcp.json --strict-mcp-config` (the real arena argv), civ6 **unapproved** | NO_CIV6_TOOLS |
| same, after `enableAllProjectMcpServers: true` (civ6 **approved**) | NO_CIV6_TOOLS |
| absolute `--mcp-config /abs/.mcp.json --strict-mcp-config` | NO_CIV6_TOOLS |
| minimal civ6-only config, **no** `env` block, `--strict-mcp-config` | NO_CIV6_TOOLS |
| minimal civ6-only config, **no** `--strict-mcp-config` | NO_CIV6_TOOLS — agent got the user-scope HTTP servers (boomtube/oracle/research) but **not** the stdio civ6 server |

So it is **not** the approval gate, **not** the `${CIV_MCP_DISABLE_LUA:-}` env relay, **not** the
relative-vs-absolute config path, and **not** `--strict-mcp-config` alone. The constant is: the
**stdio** civ6 server's tools never reach the headless `-p` agent.

## What rules out the obvious causes

- `claude mcp list` reports `civ6: uv run --directory . civ-mcp - ✔ Connected` — i.e. the server
  *does* spawn and complete a health-check handshake when claude **waits** for it.
- The server starts cleanly standalone (`CIV_MCP_DISABLE_LUA=1 uv run --directory . civ-mcp </dev/null`
  → exit 0, registers 76 tools, logs "Web API starting on :8000", shuts down on EOF).
- Port **8000 is free** on `.141` and **no** stray `civ-mcp` process is running — not a port conflict.
- FireTuner is reachable at `127.0.0.1:4318` (WSL2 mirrored networking; `ss` can't enumerate the
  Windows-side listener but a TCP connect succeeds).

## Root-cause hypothesis

The civ6 MCP **lifespan is heavy** (`src/civ_mcp/server.py`, lifespan ~L300-360): before it yields
its tools it connects to the FireTuner, starts background spectator services
(camera / popup-watcher / game-over watchdog), and launches a **uvicorn web API on :8000**. That is
appropriate for long-lived interactive / eval use, but it appears to be too slow / heavy for a
headless per-turn `claude -p` spawn — `mcp list` succeeds because it *waits* on the health check,
while the `-p` agent turn proceeds before the stdio server has registered its tools (or `claude -p`
has limited stdio-server support). Either way the CLI civ gets no tools.

> Note: the user's normal civ6 play is via **interactive** `claude`, where civ6 loads fine — which
> points the problem at the headless `-p` path / startup time, not the server's correctness.

## Consequence for the security gate

The live "`run_lua` is absent" check is currently **vacuous**: *all* civ6 tools are absent, not just
`run_lua`, so the two-hop `CIV_MCP_DISABLE_LUA` relay's runtime effect cannot be proven until the
server actually loads. (The **server-side** removal is proven deterministically: importing the server
and applying `main()`'s gate removes `run_lua` while keeping the other 75 tools.)

## What the gate DID prove (unaffected by this issue)

- **Human-safety handback**: restored to `LOCAL|0` after both the dry-run and the CLI-civ run — the
  invariant the branch exists to guarantee.
- Dry-run end-to-end: hook inject → seize P1's turn → scripted action → restore → tuner reclaim.
- Layer-1 sandbox: `--tools ""` removes Bash (agent cannot run shell `id`).
- Layer-3 need confirmed: without `--strict-mcp-config`, user-scope servers
  (serena/boomtube/Gmail/Drive/Calendar/Code-Remote) all load.
- Server-side `run_lua` removal under `CIV_MCP_DISABLE_LUA=1` (deterministic).
- Cost logging works; unit suite 32/32 on `.141`.

## Candidate fixes (in preference order)

1. **Lightweight stdio mode for the civ6 server.** Gate the web API + camera/popup/watchdog services
   behind an env (e.g. `CIV_MCP_LIGHT=1`), set by `cli_agent` for the arena spawn, so the lifespan
   yields tools immediately. Smallest, most self-contained.
2. **Raise / confirm the MCP startup wait** for the `-p` spawn (e.g. `MCP_TIMEOUT`) — but a prior test
   with `MCP_TIMEOUT=120000` did not help, so this alone is likely insufficient.
3. **Decouple tuner connection from tool registration** so tools are available immediately and the
   tuner is connected lazily on first call.

## Reproduction (on `.141`, from `~/projects/civ6-mcp`)

```bash
export PATH="$HOME/.local/bin:$PATH"
DENY="mcp__civ6__end_turn mcp__civ6__kill_game mcp__civ6__load_game_save mcp__civ6__restart_and_load mcp__civ6__load_save mcp__civ6__load_save_from_menu mcp__civ6__launch_game mcp__civ6__run_lua"
CIV_MCP_DISABLE_LUA=1 claude -p 'Call get_game_overview and state the turn number. If no such tool, reply NO_CIV6_TOOLS.' \
  --tools "" --allowedTools mcp__civ6 --disallowedTools "$DENY" \
  --mcp-config "$PWD/.mcp.json" --strict-mcp-config \
  --permission-mode bypassPermissions --output-format json --max-turns 8
# observed: result == "NO_CIV6_TOOLS"
```
