# Changelog

## v1.1.3 — Orchestrator Observability (2026-04-01)

- **Heartbeat file** (`~/.civ6-mcp/heartbeat.json`): MCP server writes phase, turn, PID on every tool call. Orchestrator reads via SSH instead of unreliable process detection.
- **Windows process killing**: PowerShell `Stop-Process` via EncodedCommand replaces `taskkill` (which can't reach Session 1 from SSH).
- **Autosave cleanup**: Clears both MCP autosaves and game `AutoSave_*` files before launch, preventing wrong-save loads. Fixed Linux path escaping for `Sid Meier's` apostrophe.
- **Phase-aware staleness**: Boot phases get 5 min threshold, playing gets 3 min.
- **Boot timeout**: 10 min hard limit for games that never reach gameplay.
- **Linux OCR**: Reverted frame extent compensation (xdotool already returns content area). Reverted combined click chain that broke GNOME input.

## v1.1.2 — Heartbeat System (2026-04-01)

- **Heartbeat file system**: Atomic JSON writes at `~/.civ6-mcp/heartbeat.json` with phase/turn/ts/pid.
- **Orchestrator reads heartbeat** instead of `Get-Process` on Windows (false negatives on schtasks sessions).
- **Status line** shows boot phase (`launching`, `connecting`, `loading`, `playing`).
- **Periodic status logging** to file every 5 minutes.

## v1.1.1 — Fleet Launch Fixes (2026-04-01)

- **Linux game launch**: `steam -applaunch 289070` replaces unreliable URI scheme.
- **Kill game safety**: `pkill -x` exact match prevents killing Steam.
- **Stale process detection**: Kill zombie game processes that block relaunch.
- **Gemini 3.1 Flash Lite** added to model catalogue.
- **Menu audit script** for per-machine OCR calibration.
- **Crash recovery**: Connection-loss auto-restart after 5 consecutive failures; HANG recovery identity check retries.
- **Non-linear ETA**: Piecewise turn-time model with Bayesian blending.
- **Sleep detection**: Resets stall timers on host wake.
- **Sentinel safety**: Completion file only created on success.

## v1.1.0 — Orchestrator + Fleet Tooling (2026-03-31)

- **Orchestrator** (`scripts/orchestrator.py`): SSH-based fleet management with job dispatch, health monitoring, stall detection, auto-retry, and Convex sync.
- **Human-readable run IDs**: `adjective-color-noun-number` format (~34M unique IDs), deterministic from model+scenario+hour.
- **Data quality gates**: `should_sync_game()` skips <10 turns, `excludeReason` field for invalid games, Elo excludes development track.
- **CORRUPTED_QUEUE fix**: Auto-clear ghost queue entries, changed narration from "load autosave" to "set new production".
- **Wrong save loading**: Screen-aware detection, autosave cleanup, multi-position click grid, Lua reload fallback.

## v1.0.4 — Production Diagnostics (2026-03-30)

- Great Person charges fix (consumed GP showed 1 remaining).
- Already-completed tech/civic detection in `set_research`.
- `CANNOT_PRODUCE` reasons in `set_city_production`.

## v1.0.3 — Crash Recovery (2026-03-30)

- Multi-retry hang recovery with identity verification.
- Autosave retention increase and hang retry guard.

## v1.0.2 — Combat + Analysis (2026-03-30)

- **Combat followup fix**: Uses estimate as fallback (Lua state stale within same turn frame).
- **Promotion XP threshold fix**: `GetExperienceForNextLevel()` replaces double-counting formula.
- **City attack errors**: Split into `ERR:OUT_OF_RANGE`, `ERR:ALREADY_FIRED`, `ERR:NO_LOS`.
- **Builder tasks**: Filter by prerequisite tech (`_LOCKED` tag).
- **Analysis tools**: Sensorium metrics, reflection-action gap analysis.

## v1.0.1 — Combat Visibility (2026-03-29)

- Combat visibility improvements, movement diagnostics, trade reporting.

## v1.0.0 — Initial Release (2026-03-29)

- MCP server with 77 tools for Civilization VI via FireTuner.
- CivBench eval infrastructure with Inspect AI integration.
- Convex dashboard with real-time game tracking, Elo rankings, strategic map replay.
- Azure Blob telemetry storage.
- Support for macOS, Windows, and Linux.
