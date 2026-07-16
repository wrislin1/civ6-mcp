# Seat-zero attended live-gate evidence

> **Status 2026-07-15T03:16:46Z: BLOCKED before watcher startup.** The
> hardened branch is locally verified at `ac32ce1f2e95c9a16de5985cecfc9a8f6bd979cc`,
> but the gaming PC checkout is `main` at
> `480fc8d0d3ac21e00ff7642ce8fa10a0e319b715` and does not contain the
> hardened commit object. Civilization VI is not running and no FireTuner
> listener is available. No remote checkout change, watcher start, save load,
> direct Lua fabrication, or live PASS occurred.
>
> **Update 2026-07-15 (post-merge):** the hardened branch (after a second
> review fix wave, tip `0272cc9`) was merged to main at `845ae09` with riz's
> explicit direction ("merge push"); 1194 tests green. The rerun is unblocked
> once the gaming PC checkout is fast-forwarded to ≥ `845ae09`. Gates below
> remain NOT EXERCISED.
>
> **Update 2026-07-15 (attended rerun): gates 1 and 2 PASSED live** on the
> gaming PC (main at `bf56e6f`, after the third review fix wave `d4cbaad`).
> See Gate status below. Gate 3 (hard-block human escape) is still pending.

## Offline verification

| Command | Observed result |
|---|---|
| `uv run --extra test pytest tests/arena/test_hook.py tests/arena/test_seat0.py tests/arena/test_coordinator.py tests/arena/test_analyze.py tests/arena/test_orphan_sweep.py -q` | PASS: 263 passed in 48.32s |
| `uv run --extra test pytest tests/arena -q` | PASS: 965 passed in 49.37s |
| `uv run --extra test pytest tests/test_parsers.py tests/test_save_scumming.py -q` | PASS: 78 passed in 0.05s |
| `uv run --extra test pytest tests/ -q` | PASS: 1174 passed in 49.50s |
| `git diff --check` | PASS: exit 0, no output |
| `git status --short --branch` | PASS before evidence update: `## arena-seat0-piloting` |

Final verification after recording the blocked evidence reran
`uv run --extra test pytest tests/ -q`: 1174 passed in 49.45s.

## Authority audit

The required `rg` audit was run against `src/civ_mcp/arena` and
`src/civ_mcp/lua/notifications.py`.

- `ACTION_ENDTURN` is built only by `build_end_turn()` and dispatched only by
  `hook.end_turn()` through InGame `execute_write`; the coordinator has the
  sole `await hook.end_turn(conn)` call.
- Arena policy tool tiers contain research and production tools but no
  `end_turn`; CLI policy lockdown also denies `mcp__civ6__end_turn`.
- Valid seat-zero `AI_PROCESSING` reaches the quiet drain branch: GameCore poll,
  one-second sleep, and no InGame operation. The orphan sweep remains below
  that branch in ordinary human-idle handling.
- `ARENA_SEAT0_AUTOMATION_FAILURE` has no registered strategic resolver, is
  classified as hard, and reaches the idempotent `seat0_human_pending` path
  without a strategic default.
- Research and production choices remain policy-owned: the deterministic
  scripted policy makes its own choices, while the seat-zero mechanical core
  only finishes units, acknowledges informational prompts, or clears a stale
  notification after Lua proves the underlying policy choice is already set.

## Read-only live preflight

| Check | Observed fact |
|---|---|
| `tools/skills/civ6-arena-live/scripts/firetuner-owner-map.sh` | No local or gaming-WSL arena/MCP owner and no local/remote/Windows socket on port 4318. The listed local Claude processes are this development session, not FireTuner owners. |
| `tools/skills/civ6-arena-live/scripts/arena-live-status.sh` | Remote branch `main`, SHA `480fc8d0d3ac21e00ff7642ce8fa10a0e319b715`; no watcher process; final safe hook probe returned `ConnectionError: Cannot connect to Civ 6 at 127.0.0.1:4318`. |
| Remote `git cat-file -e ac32ce1f...^{commit}` | Exit 128: hardened commit is not present in the remote object database. |
| Windows `tasklist.exe /FI 'IMAGENAME eq CivilizationVI.exe'` | `INFO: No tasks are running which match the specified criteria.` |
| Local and remote run-id search | No artifact matched `seat0-scripted-20260715` or `seat0-llm-20260715`; neither watcher was started. |

The remote checkout also has pre-existing untracked arena artifacts and helper
files. They were inspected read-only and left untouched.

## Gate status

> **Update 2026-07-15 (attended rerun on the gaming PC, main at `bf56e6f`):**
> gates 1 and 2 ran live and PASSED. Gate 3 remains not exercised.

| Gate | Run ID | Status | Save / transcript evidence |
|---|---|---|---|
| Scripted mixed-seat | `seat0-scripted-20260715` | **PASS 2026-07-15** | `arena_runs/seat0-scripted-20260715/{transcript.jsonl,report.md}`. 8 seat-zero turns (T1–T8), all `played`/`advanced`, 1 end-turn request each, 0 blockers, 0 repairs; autosaves `0_MCP_0001`–`0_MCP_0008` adopted. Driver mix `scripted=8, in_process=16`. Exit 0 on budget exhaustion (24 slots). |
| LLM seat zero | `seat0-llm-20260715` | **PASS 2026-07-15** | `arena_runs/seat0-llm-20260715/{transcript.jsonl,report.md}`. 12 cli-claude seat-zero turns (T9–T20), all `played`/`advanced`, 1 end-turn request each, 0 repairs, 0 failed turns; autosaves `0_MCP_0009`–`0_MCP_0020` adopted. T18–T20 each hit `ENDTURN_BLOCKING_UNITS`, cleared by mechanical `finish_units` cleanup (post-cleanup snapshots empty, no refire). Driver mix `cli=12, in_process=24`. Cost $9.73. |
| Hard-block human escape → full-LLM-control autonomy (redefined per riz 2026-07-15: "blockers should only be things the llm literally cannot do itself") | `seat0-hardblock-20260715-leg1b/leg2/leg3b/leg4` | **PASS 2026-07-15** | Late-game attended save (Korea T298+, 12 civs, Modern era). Escape hatch proven live twice BEFORE the autonomy gaps were closed: leg1b T301 (quiet non-advance) and leg2 T303 (WC bounce) both terminalized `human_pending` with CRITICAL logs and clean waits. Each escalation was then converted into automation (diplomacy-wedge pass, guarded refire, WC gate + resume-submit, mechanical spy escape/governor idle) — final leg4: 8/8 turns T306–T313 `played`/`advanced`, 1 end request each, **5 live diplomacy-wedge saves** (AI deals from Poland/Egypt/Ethiopia answered by the pilot on attempt 1), 0 human escalations, $18.67. Also recovered live: T306 Macedon AI hard-hang via `0_MCP_0306` Lua load (drain deadline CRITICAL + `turn_kind=failed` recorded correctly; loader bugs fixed in `0c72cca`). |

### Full-LLM-control fix waves (2026-07-15, driven by live gate escalations)

| Commit | What the live run exposed → what shipped |
|---|---|
| `d4cbaad` | Review-3 wave: 10 verified findings (drain safety, accounting, recovery). |
| `bf56e6f` | Gate-1 run: report driver-mix line hid the `scripted` bucket. |
| `b35bf93` | Deal wedged admission 20+ min → diplomacy-wedge pass (idle+drain), spy escape/governor idle mechanical, reactive diplomacy tools in every tier. |
| `dd66f93` | T301 quiet stall → recheck session probe, ONE guarded refire, human-pending probe. |
| `a118341` | T303 WC bounce → pre-fire WC gate + default voter; era-neutral pilot prompts. |
| `5cc43a1` | T303 replay pre-fire block → resume-congress mechanical submit; played-path WC clears-at-end. |
| `0c72cca` | T306 Macedon hang recovery → load_game_save Lua tier on WSL, extensioned-name match, engagement verification. |

### Seat-zero turn records

Gate-1 and gate-2 seat-zero turn rows, terminal states, recovery-save names,
and blocker snapshots are recorded in the run transcripts listed above (grep
`"player_id": 0`). Operational notes from the rerun:

- The FireTuner port is single-client: the dev session's own `civ-mcp` MCP
  server held 4318 and had to be killed before `civ-arena` could connect.
- A double-launch of gate 2 killed the first instance after one $1.02
  cli-claude turn (no transcript row); the second instance ran to completion.
  Never launch a second `civ-arena` while one is backgrounded.

## Authority/state required to resume

1. Explicit riz direction to land the hardened feature branch on `.141`
   (feature-ref push plus remote `git merge --ff-only`); file copying is not an
   acceptable substitute.
2. Civilization VI running with `EnableTuner=1` and an appropriate mixed-seat
   save loaded on the gaming PC.
3. For the human-escape gate, an attended real save/state exposing an unsupported
   blocker such as `ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE`.

All three gates passed on 2026-07-15 (see Gate status). Gate 3 was redefined
mid-day under riz's full-LLM-control directive and passed at main `0c72cca`
after four fix waves driven by live escalations (deal wedge, guarded refire,
WC gate, resume-congress submit, load_game_save Lua tier).
