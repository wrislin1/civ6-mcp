---
name: civ6-arena-live
description: Use when operating or debugging live civ6-mcp arena watcher runs on the gaming PC, including human handoff, puppet turns, Codex CLI civs, FireTuner, and cleanup.
---

# Civ6 Arena Live

## Overview

Live arena operation has two separate states: the Civilization game turn state and the external watcher process state. Verify both before telling the user to end a turn or before claiming the game is safely back to the human.
Claude loads this skill from `.claude/skills/civ6-arena-live` (gitignored). Keep that
path a symlink to `tools/skills/civ6-arena-live`, as `.agents/skills/` already is: a
copied directory there silently shadows every later edit (a July copy is what loaded on
2026-10-09, without this file's benchmark sections).

## Environment

- WSL gaming PC: `riz@192.168.20.141`
- WSL repo: `~/projects/civ6-mcp`
- Native Windows companion checkout: `C:\Users\wrisl\dev\civ6-mcp`
  (`/mnt/c/Users/wrisl/dev/civ6-mcp` from WSL). It is a GitHub clone, not a
  separate codebase.
- Known-good hybrid watchers (both verified live):
  - Codex: player 1 `local:qwen3-coder:30b`, player 2 `cli-codex:gpt-5.5`
  - Claude: player 1 `local:gemma4:26b`, player 2 `cli-claude:` (empty model = Claude default)
  - both with `--max-puppet-turns 2` and `--idle-poll-limit 1800`

## Operating Pattern

When the requested save is not already verified in-game, **REQUIRED
CONDITIONAL REFERENCE:** read
[references/windows-launcher.md](references/windows-launcher.md) before
starting a watcher. It covers the native companion checkout, Windows
Application Control, OCR loading, exact-state verification, and FireTuner
release.

CLI-provider pre-flight (when a watcher uses `cli-claude` or `cli-codex`), on the target host before arming — a failed seat only surfaces mid-handoff otherwise:

1. The provider binary is on PATH **and authenticated** (e.g. a trivial `claude -p` / `codex exec` actually returns). A host that has only ever run one provider may not have the other set up.
2. The gateway is reachable and the local model id is actually served (`curl .../v1/models`) — model names drift (`gemma4:26b`, not `gemma4-26b-cpp`).
3. `.mcp.json` is present in the repo CWD (project auto-discovery needs it).
4. Tip: a diagnostic `claude -p` calling `get_game_overview` with no game loaded should return the FireTuner `4318` connection error — that proves the civ6 tools load on this host.

### Preregistration gate (added after v8, 2026-08-30)

For every recorded experiment, resolve and record the actual endpoint for
each configured model/gateway pair before launch; green health alone is not
enough. Confirm that unrelated local-LLM and benchmark jobs use
non-overlapping endpoints. For example, if `living-emerald` is active, verify
and record that it is actually pinned to `home-llm` rather than the experiment
gateway; this is an example, not a permanent allocation. Warm every configured
model on its resolved endpoint and record representative response latency.
**Block launch** until the endpoint allocations are non-overlapping and all of
this evidence is recorded.

If overlap is discovered post hoc, treat latency and timeout comparisons as
confounded. A complete, timeout-free behavioral artifact need not be
discarded, but behavior affected by timeouts, fallback, truncation, or
incomplete turns is also confounded.

Before launching an overnight config-driven run, land BOTH in the same
commit as the experiment YAML:

1. A config pin test asserting the intentional deltas from the previous
   run (pattern: `test_arena_channels_behavior_v8_is_standard_tier_bundle_
   delta_from_v7` in tests/arena/test_experiment.py).
2. For every treatment the config claims, an assertion that the mechanism
   can actually fire. Briefing specifically: assert
   `briefing_budget(n_ctx, options, 0, 0) > 0` with the gateway's real
   n_ctx — v8 "enabled" briefing but max_steps 15 x result_char_cap 6000
   reserved 38,704+ tokens against 32,768, so briefing rendered zero
   tokens all run and nobody noticed until post-hoc review.

Before telling the user to end turn:

1. Check for existing `civ-arena`, `codex exec`, and `civ-mcp` processes.
2. Start exactly one watcher if none is intentionally running.
3. Confirm it is alive and record `RUN_ID`, `PID`, `OUT`, and `ERR`.
4. Only then tell the user to end the turn.

For the channels-behavior line, use the committed experiment config rather
than the script's bare four-seat defaults, for example:

```bash
tools/skills/civ6-arena-live/scripts/start-hybrid-watch.sh \
  --config experiments/arena-channels-behavior-v6.yaml
```

The v6/v7 configs include scripted seat 0, so they do not require a human to
advance the player civ after the save is loaded. Do not combine `--config`
with `--run-id` or config-owned player, budget, idle, or gateway overrides.
The YAML's top-level `run_id` owns both the transcript directory and detached
watcher log names. Before launch, confirm no exact artifact already exists for
that ID; suffixed archival names such as `*-paused-t161` do not collide.

After the user says it is back to them:

1. Read the watcher output and cost tail.
2. For an ad-hoc handoff cycle, confirm `puppet_turns_played: 2`. For a
   config-driven experiment, confirm the config's own budget or stop condition.
3. Confirm hook state is `PuppetState(local=0, active=False, ...)`.
4. Confirm no arena/Codex/MCP processes remain.
5. Start the next watcher only if the user wants the next cycle armed.

### Resume budget accounting

`max_puppet_turns` is a shared admission budget, not a transcript-row count.
Every configured seat charges it, including channel-only scripted seats that
emit no transcript row; an admitted seat-0 turn that ends `human_pending` was
also charged before repair. Therefore never subtract `wc -l transcript.jsonl`
or only successful rows to create a resume config.

For each completed final-timeline game round, subtract the number of configured
seats (`len(civs)`; four for channels v6/v7). Account for any partial round from
the watcher/coordinator admission order, including transcriptless seats. If the
exact partial-round charges cannot be proven, derive the remaining observation
window from the loaded save's game turn and document the uncertainty; do not
guess from transcript rows. Preserve the YAML `run_id`, archive the stopped log
triplet with a suffix, and change only the two budget fields in an ignored
operational resume config.

## Important Invariants

- An ad-hoc handoff watcher is per-cycle and normally exits after
  `--max-puppet-turns 2`; it is not a daemon. A config-driven experiment uses
  its committed budgets instead (channels v6/v7 use 120 puppet turns), so do
  not apply the two-turn completion check to it.
- `PuppetState(local=0, active=False)` means human control is back.
- A direct hook poll can fail while an arena or Codex MCP child owns the single FireTuner connection. Do not treat that alone as proof Civ is down.
- End-of-session means no watcher process is running unless the user explicitly asks to keep one armed.

## FireTuner Single-Client Diagnostics

FireTuner allows one client. Before any direct `GameConnection()`, hook poll,
or live-probe script, map ownership first:

- local riz-llm: `ss -tnp | grep 4318`, `pgrep -af 'civ-mcp|civ-arena|ssh.*4318'`
- gaming WSL: `ss -tn | grep 4318`, watcher/MCP process scan
- Windows side: `NETSTAT.EXE -ano | grep 4318`

If any `ESTABLISHED`, `CLOSE_WAIT`, or `FIN-WAIT` socket exists on `4318`, do
not open a direct hook poll. It can compete for the single slot and leave stale
loopback sockets. Use the existing owner instead:

- Claude/Codex-spawned `civ-mcp`: `curl http://127.0.0.1:8000/api/overview`
- arena watcher: read watcher logs/cost tail; avoid a separate hook poll

No WSL process owner does not prove the slot is free; mirrored networking can
show Windows loopback sockets without an owning WSL process.

After merging new code, restart any already-running `civ-mcp` before expecting
new tools or code paths. A successful `/api/overview` proves game connectivity,
not that the process has freshly loaded code.

## Landing code

`origin` is GitHub (`git@github.com:wrislin1/civ6-mcp.git`). Land work by
committing to `main` and running `git push origin main`. There is no `.141`
checkout in the loop anymore; if another machine such as riz-llm needs the
code, it pulls from GitHub.

## Scripts

Run from the repo root:

- `tools/skills/civ6-arena-live/scripts/arena-live-status.sh`
- `tools/skills/civ6-arena-live/scripts/firetuner-owner-map.sh` — read-only
  process/socket/API map for FireTuner ownership. Run this before any direct
  hook poll when connection state is ambiguous.
- `tools/skills/civ6-arena-live/scripts/start-hybrid-watch.sh`
- `tools/skills/civ6-arena-live/scripts/stop-arena-watchers.sh`
- `tools/skills/civ6-arena-live/scripts/windows-civ6-launcher.sh` — WSL entry
  point for native Windows `preflight`, `load`, and `restart-and-load`.
- `tools/skills/civ6-arena-live/scripts/sync-windows-checkout.sh` — fast-forward the
  Windows companion checkout from the WSL repo (no GitHub push); run after every
  archive commit and before `capture`.
- `tools/skills/civ6-arena-live/scripts/benchmark-stage.sh` and
  `benchmark-family-chain.sh` — authoring stage runner with logging; the chain commits a
  new archive and syncs Windows.
- `tools/skills/civ6-arena-live/scripts/identity-impact.py` — fingerprint vs toolkit
  classification of a change range, plus the current identities.
- `tools/skills/civ6-arena-live/scripts/probe-gamecore-api.py` — live GameCore accessor
  presence check (needs the free tuner slot and an in-world game).

The stop script is dry-run by default; pass `--yes` to terminate matching watcher process groups.

## Live-run stall playbook (proven 2026-08-30, v7)

Three recurring seat-0 stall classes and their live fixes, until the code
fixes land (drain-arm orphan sweep, refire modal dismissal, production
readback):

1. `seat0_drain_deadline` after channel funding: AI↔AI diplomacy sessions
   wedge the interturn. Let the watcher hit its deadline (clean self-stop),
   then run the sweep over the freed tuner (InGame state; no reload needed,
   funding survives):

   ```bash
   uv run python - <<'PY'
   import asyncio
   from civ_mcp.connection import GameConnection
   from civ_mcp.lua.diplomacy import build_close_orphan_sessions
   async def main():
       conn = GameConnection(); await conn.connect()
       print(await conn.execute_write(build_close_orphan_sessions()))
       await conn.disconnect()
   asyncio.run(main())
   PY
   ```
2. `human_pending` with `blockers=[]`: an engine UI modal (disaster cinematic,
   Historic Moments, Inspiration/Eureka) is blocking. One synthetic ESC per
   modal clears it; LoadScreen/modals accept VK_ESCAPE only. A 64-bit
   SendInput INPUT struct must include MOUSEINPUT in the union (sizeof 40)
   or every event is rejected with error 87.
3. `human_pending` with a persisting `ENDTURN_BLOCKING_PRODUCTION` after a
   repair that claims success: verify with `GetCurrentProductionTypeHash()`
   per city over the tuner; refill any `hash == 0` city via
   `GameState.set_city_production(...)`, then relaunch — the fresh
   coordinator re-admits the held turn and ends it itself.

4. **Display-detach boot wedge** (2026-08-30): if the physical display
   powers off, Windows drops to the `WinDisc` ghost display; the running game
   wedges in `AppHost::WndProcEx ... resizing buffers` (tuner dies) and every
   relaunch wedges at boot (~frame 3, one core spinning, no Auto HDR event).
   Pixels are useless (black captures); read
   `AppData\Local\Firaxis Games\...\Logs\Profile.csv` (fresh-offset frame
   counter) and `Renderer.log`. Fix: force-attach a detached GPU output with
   `ChangeDisplaySettingsExW(output, mode, CDS_UPDATEREGISTRY|CDS_NORESET)`
   per output, then a final global apply — primary flips from `WinDisc` to a
   real `\\.\DISPLAYn` and boots complete. `DisplaySwitch.exe`,
   SC_MONITORPOWER, and execution-state keepers do NOT re-attach.

   On this PC the confirmed trigger chain is broader than idle sleep: an Auto
   HDR/display-mode transition can briefly disconnect the TV, Home Assistant
   interprets the TV as powered down, and its automation then powers off the
   DENON AVR. That converts a transient HDMI handshake into a real display
   detach. A Windows execution-state keeper does not prevent this. Debounce
   the automation so AVR power-off requires the TV to remain explicitly
   `off` for a grace period; ignore `unavailable` and brief off transitions.

5. **WC-results transition hold** (v8 T182; fixed path proven in v9): map
   `NOTIFICATION_WORLD_CONGRESS_RESULTS` to
   `ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK`, let the seat-0 WC default/recheck
   path run, then permit the guarded refire after a quiet radar. v9 advanced
   T181 -> T182 through this path without a reload. If the notification still
   persists, production passes CanProduce but fails readback, or end-turn is a
   no-op, reload that turn's `AutoSave_NNNN`; fresh deserialization remains
   the proven fallback for the deeper v8 mid-segment state.
6. **Boot roulette** (v8): cold boots hang probabilistically at ~frame 3-5
   (one core spinning, no Auto HDR event in the System log). Verify boot
   health with a fresh-offset `Profile.csv` frame check (healthy = frames
   past 100 within ~4 min); on a wedge, kill and relaunch. A detached
   display (`WinDisc` primary) makes the hang deterministic — force-attach a
   real output first (see class 4).
7. **Continue/leader screen input**: the bridge `press-escape` can be
   ignored there; the reliable press is ALT-tap + SetForegroundWindow +
   SendInput ESC with `wScan` populated (MapVirtualKeyW). Clicking works too
   but only at OCR-derived coordinates: window origin + line (x + w/2,
   y + h/2) from `_ocr_game_window`; screenshot-pixel guesses miss (the shot
   file is downscaled).

Resume budgets: remaining rounds × configured seats (4 for channels v6-v9),
computed from the live game turn — see "Resume budget accounting" above.

## Benchmark runner sessions (learned 2026-09-26)

- **Stop the Claude session's own `civ-mcp` before any benchmark CLI** (`civ-arena-benchmark`,
  `civ-arena-benchmark-position`). Its background watchers auto-reconnect the moment the game is
  up and hold the single FireTuner slot; native and WSL probes are both refused while it lives.
  `kill -9` the `civ-mcp` python and its `uv run` parent; the session loses the civ6 MCP tools.
- **Launch from WSL** when the MCP `launch_game` is unavailable:
  `cd /mnt/c/Users/wrisl && cmd.exe /c start "" "steam://run/289070"`, then
  `civ6_launcher_bootstrap.py boot-health --json`. `restart-and-load <save>` through the bootstrap is
  the full kill → launch → OCR menu load → classify-gated Escape route.
- **The tuner port is open on the continue screen and the load menu** (frontend Lua states only). A
  connection that reports "GameCore_Tuner/InGame states not found" means a frontend screen, not a
  dead game; drive it with `classify-frontend` + `press-escape`.
- **Runner credentials:** `LITELLM_OPENAI_API_KEY` must be non-empty even for the unauthenticated
  llama-swap endpoint; source `~/.config/riz-llm/.env` into the runner's environment only. The
  registry host `home-llm` is 192.168.20.146 (ssh alias); the runner reaches it by that bare name for
  GPU evidence.
- **Boot health on an idle game** passes via the responsive-window fallback (Profile.csv logs only
  slow frames); a fresh-frame pass needs the game to be loading or animating.
- **Hand audits are semantic reads**, never a replay of the scorer's conventions. A scorer correction
  changes the fingerprint and forces a campaign re-freeze and full rerun (~2 h for two 24-trial blocks).
- **A minimized game window** (`-32000,-32000`, 0x0) makes OCR fail and the screen classifier read
  `unknown`; the classifier now restores it, but if a reload wait stalls on `unknown`, check window
  state first (`ShowWindow SW_RESTORE`) before assuming the game is hung.
- **Hands off the gaming PC during a run** (learned 2026-09-28). Operator mouse use minimized the game
  window and stole focus; the Escape press is refused unless Civ is foreground, so the continue screen was
  never dismissed, three reload attempts failed, and the run aborted. Leave the machine alone until the
  runner exits.
- **The attempt budget persists per run id.** After `attempts_exhausted` (3 infrastructure attempts) a
  same-id resume aborts immediately by design. For a non-counting pilot, rerun the frozen round in full under
  a new run id and retain both directories; for a counted campaign the lock/resume rules apply — do not
  reset attempts by hand.
- **`restart-and-load` works from a dead game** (Civ exited): it reports `Kill: Game is not running`, launches,
  OCR-selects the save, and classify-gates the Escape. Verify the canonical digest against the capture before
  rerunning anything.

### Plan 3 authoring stages (learned 2026-10-09)

- **GameCore is not InGame.** Setup ops and the v2 capture run in `GameCore_Tuner`, where
  `CityDistricts` has no `Members()`, an empty build queue reads as the string `"NONE"`,
  a killed unit lingers as a delayed-death row, and no build-charge setter exists. Every
  family's first live contact failed on one of these. Before a scenario clock opens, run
  `scripts/probe-gamecore-api.py` for every accessor the recipe relies on and check it
  against [references/gamecore-api-surface.md](references/gamecore-api-surface.md).
- **Commit the archive, sync Windows, then `capture`.** The bridge deploys the archive by
  repo-relative path resolved in the Windows checkout. `scripts/sync-windows-checkout.sh`
  fast-forwards it straight from the WSL repo, no GitHub push needed;
  `scripts/benchmark-family-chain.sh` does this itself after a passing `archive` stage.
- **Run stages through `scripts/benchmark-stage.sh`.** The authoring CLI configures no
  logging, so a 10-20 min stage is silent. The wrapper adds INFO logging, drops the
  per-command connection chatter, and tees a log under `benchmark_runs/logs/`, never
  inside an attempt dir (`evidence_index_complete` rejects scratch there). It filters
  with `grep -a`: a plain `grep -v` on this stream reports "binary file matches" once
  and the tee'd log stays empty.
- **Classify a fix before amending anything.** Only `FINGERPRINT_DEPENDENCIES` move
  `code_identity` and force a family to repeat from `survey`; toolkit files
  (`benchmark_authoring.py`, `game_launcher.py`, `game_lifecycle.py`, ...) never do.
  `scripts/identity-impact.py <range>` classifies the changed files and prints the
  current identities next to the preflight record. Misclassifying the launcher fix cost
  an amended commit and a Windows checkout reset.
- **A cold frontend save list stalls the game.** Right after launch, the tier-0
  (frontend Lua) load's `UI.QuerySaveGameList` over ~300 OneDrive saves left the game
  deaf to tuner commands for ~4.5 min, and the 30 s cancel landed after the late result
  had already fired `Network.LoadGame` (probe 1 failed on the continue screen). Get
  in-world first (bridge `press-escape` on the continue screen); every in-session reload
  then takes the warm tier-1 path (~45 s). Never send ESC in-world: it toggles the pause
  menu. The game answers the tuner at full speed while unfocused.
- **Recipe facts.** Every required fact must be discoverable inside `result_char_cap`
  (1500 chars) of the tool result that carries it, or `archive` fails on it. When several
  selectors must agree on geometry (attacker, civilian, archer, escort), author one joint
  layout setup op that scans for a consistent tile set and reads back every position;
  independent selectors cannot see each other. Freeze damage thresholds from measured
  probe shots, not from the combat table.
