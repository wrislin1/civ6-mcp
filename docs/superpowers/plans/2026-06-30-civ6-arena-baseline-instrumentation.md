# Plan: Civ6 Arena — Baseline Instrumentation & 4-Civ Decision-Quality Run

> Add behavior-neutral per-turn transcript capture to all arena policies, correct the local backend
> to llama.cpp, then run a measured 10–20 round 4-civ baseline and report decision quality.

**Status:** Ready for implementation
**Branch / worktree:** `arena-baseline-instrumentation`
**Test baseline (verified):** `uv run pytest tests/arena -q` → **42 passed**
**How to execute:** TDD per task — add the failing test first (observe the expected RED), implement until
GREEN, then commit. One commit per task. Tasks 1–6 are offline/unit work; Tasks 7–11 are live/operational.

---

## Context

The arena (`src/civ_mcp/arena/`) drives Civ 6 AI civs via three policies: `cli-claude` (`claude -p`,
full 76-tool MCP + `AGENTS.md` guide), `cli-codex` (`codex exec`, same), and `local` (in-process
`LLMPolicy`). Goal: improve local-model decisions (Qwen made poor moves; Gemma ran one turn). Step 1 is
to **understand and measure** before changing anything.

**Investigation finding — asymmetric harnesses:**

| | CLI civs | Local civs (`agent.py: LLMPolicy`) |
|---|---|---|
| Prompt | full `AGENTS.md` guide | 4-sentence `SYSTEM` constant |
| Tools | 76 MCP tools + advisors + map | **9** tools; **no map**, no advisors |
| Data | narrated text (`narrate.py`) | `str(GameOverview/UnitInfo/CityInfo)` raw `@dataclass` repr, **truncated at 1500 chars** |
| Steps | `--max-turns 40` | `max_steps=6` |

Local civs are **map-blind, fed truncated dataclass dumps, no advisors** — "poor decisions" are largely
harness starvation. Second finding: `config.py` points local civs at `…:11430` = riz-llm's **Ollama**
GPU0 — i.e. local has been running on **Ollama, not llama.cpp**. The llama.cpp path is LAN-exposed via
**llama-swap unified `http://192.168.20.196:11444/v1`** (verified: serves `gemma4-26b`, `qwen3.6-27b`
as gguf, auto-swaps). LiteLLM `:4000` is localhost-only, so `:11444` is the correct target.

## Decisions (locked with user)

1. **Baseline current reality first** — instrument, then run; **no model-behavior changes**. Preserve and
   *log* the repr/truncation/9-tool limits; do not fix them here. Fixes = a follow-on plan.
2. **Symmetric full transcripts** — full tool calls/args/untruncated results + timing + tokens for all
   civs, including CLI civs' internal steps (Claude → streaming output; Codex already streams).
3. **llama.cpp, not Ollama** — local civs use llama-swap `:11444`, slugs `gemma4-26b` / `qwen3.6-27b`.
4. **One shared 4-civ game**, 10–20 rounds (early interaction minimal → fair head-to-head).

## Scope

**In:** additive transcript instrumentation (both policy paths + coordinator) with a coordinator-owned
`TranscriptSink`; llama.cpp endpoint correction + all-local-backends reachability check; offline
analysis/report; tests; a 2-round shake-out then a 10–20 round baseline; a findings report.
**Out (follow-on):** richer local prompt, map tool/advisors for local, narration vs repr, higher `max_steps`.

## Design decisions resolved from plan review

- **Transcript ownership:** policies are pure — they only attach a `result["transcript"]` payload and
  never touch a sink. The **coordinator** is the sole writer (it owns `conn`/`gs` for the state delta).
  So `LLMPolicy`/`CLIAgentPolicy` get **no** `transcript` ctor arg.
- **Run directory:** `_run` creates `arena_runs/<run_id>/` with `mkdir(parents=True, exist_ok=True)`
  **before** constructing `CostLog` (whose `open(self.path,"a")` at `cost.py:34` does not create parents),
  regardless of whether transcripts are enabled.
- **run_id default:** reuse `civ_mcp.run_id.generate_run_id(model_id="arena")` (run_id.py:310) — no invented scheme.
- **All local backends checked:** `build_policies` returns **all** local backends; `_run` reachability-checks
  each (today it overwrites `in_proc_backend`, checking only the last — fatal with two local models).
- **Bootstrap-free state snapshot:** the coordinator must NOT call `gs.get_game_overview()` (it bootstraps
  `_last_snapshot` at game_state.py:103 → not behavior-neutral). Use a direct overview query helper.
- **Parser ordering:** Task 3 ships **defensive** parsers + provisional/synthetic fixtures; Task 9
  captures real `claude`/`codex` stdout and **finalizes** the parsers + re-asserts.

## File Structure

New: `src/civ_mcp/arena/transcript.py`, `src/civ_mcp/arena/analyze.py`,
`tests/arena/test_transcript.py`, `tests/arena/test_analyze.py`,
`docs/superpowers/plans/2026-06-30-civ6-arena-baseline-instrumentation.md`.
Modified: `arena/agent.py`, `arena/cli_agent.py`, `arena/coordinator.py`, `arena/arena.py`,
`arena/config.py`, `tests/arena/{test_cli_agent,test_agent,test_coordinator,test_arena_wiring,test_config}.py`,
`tools/skills/civ6-arena-live/scripts/start-hybrid-watch.sh`, `pyproject.toml`.

## Transcript record schema (one JSONL line per puppet player per puppet turn)

`arena_runs/<run_id>/transcript.jsonl`; `arena_cost.jsonl` co-located.

```jsonc
{ "schema_version":1,"run_id":"...","ts":"...","player_id":3,"turn":14,
  "provider":"local","model":"qwen3.6-27b","driver":"in_process",   // or "cli"
  "steps":[{"idx":0,"role":"assistant"|"tool","text":"...|null","tool_name":"get_units|null",
            "tool_args":{...},"tool_result_full":"<UNTRUNCATED>","result_total_chars":8423,
            "result_chars_fed_to_model":1500,"truncated":true,"ts_start":0.0,"ts_end":0.0,
            "prompt_tokens":0,"completion_tokens":0}],
  "final_summary":"...","prompt_tokens":0,"completion_tokens":0,"usd":0.0,"wall_clock_s":0.0,
  "step_count":4,"max_steps_reached":false,
  "invalid_tool_calls":[{"step":2,"tool_name":"build_wonder","reason":"unknown_tool","raw_args":"..."}],
  "cli_exit":0,"cli_stderr_tail":"","state_before":{...},"state_after":{...},
  "state_delta":{"score":3,"gold":-20,"cities":1,"units":0,"science":1.2,"research":"...","civic":"..."} }
```

**Behavior-neutrality invariant (asserted in tests):** the local model still receives `str(result)[:1500]`;
`_parse_claude`/`_parse_codex` return identical `(summary,in,out,usd)` after the stream-json switch.
Transcripts are an *additional output*, never an input.

---

## Tasks

### Task 0 — Worktree + plan copy
- [ ] Create the worktree/branch (`superpowers:using-git-worktrees`): `arena-baseline-instrumentation`.
- [ ] Create the in-tree plan directory and copy this plan verbatim:
  ```bash
  mkdir -p docs/superpowers/plans
  cp /home/riz/.claude/plans/writing-plans-in-the-last-rosy-barto.md \
    docs/superpowers/plans/2026-06-30-civ6-arena-baseline-instrumentation.md
  ```
- [ ] Commit: `chore(arena): plan — baseline instrumentation`.
- Verify: `git status --short` shows only the new plan doc before commit, then clean after commit.

### Task 1 — `TranscriptSink` module
- [ ] RED: `tests/arena/test_transcript.py` — `write()` appends valid JSONL; `for_run(run_id)` makes the dir
      and returns a sink with `path == arena_runs/<run_id>/transcript.jsonl`; `NullSink().write({})` writes nothing.
      Run `uv run pytest tests/arena/test_transcript.py -q` → expect **import/attr error (RED)**.
- [ ] GREEN: new `src/civ_mcp/arena/transcript.py`:
  ```python
  from __future__ import annotations
  import json, os
  class TranscriptSink:
      def __init__(self, path: str): self.path = path
      def write(self, record: dict) -> None:
          with open(self.path, "a") as f: f.write(json.dumps(record) + "\n")
      @classmethod
      def for_run(cls, run_id: str, base: str = "arena_runs") -> "TranscriptSink":
          d = os.path.join(base, run_id); os.makedirs(d, exist_ok=True)
          return cls(os.path.join(d, "transcript.jsonl"))
  class NullSink:
      def write(self, record: dict) -> None: pass
  ```
- [ ] Commit: `feat(arena): TranscriptSink/NullSink`.
- Verify: `uv run pytest tests/arena/test_transcript.py -q` → pass.

### Task 2 — Local path instrumentation (`agent.py: LLMPolicy`) — payload only, no sink
- [ ] RED: extend `tests/arena/test_agent.py` (fake backend returning a tool call then a text reply):
  assert tool message content `== str(result)[:1500]` and `actions[i]` keeps `[:300]` (unchanged), AND
  `result["transcript"]["steps"][i]["tool_result_full"]` is untruncated with
  `result_chars_fed_to_model == min(len, 1500)`; an unknown tool name appears in `invalid_tool_calls`
  with `reason="unknown_tool"` while dispatch still returns its `ERROR: ...` string. Expect **RED** (no `transcript` key).
- [ ] GREEN in `LLMPolicy.__call__` — **do not** add a ctor arg; keep every `messages`/`actions` line byte-identical:
  - `import time`; add `_KNOWN_TOOLS = frozenset({...the 9 dispatch keys...})` near `_dispatch`.
  - Accumulate `steps`, `invalid_tool_calls`; per call `ts_start=time.time()` before `backend.chat`, `ts_end` after dispatch.
  - For each tool call: classify unknown name / `json.loads(arguments)` failure into `invalid_tool_calls`
    (observation only — leave the existing `try/except` dispatch untouched). Build the step from the **same**
    `result` object already stringified: `tool_result_full=str(result)`, `result_total_chars=len(str(result))`,
    `result_chars_fed_to_model=min(len, 1500)`, `truncated=len>1500`, plus `reply.prompt_tokens/completion_tokens`.
  - At each `return`, add `"transcript": {"steps":steps,"invalid_tool_calls":invalid,"wall_clock_s":...,
    "max_steps_reached":...,"final_summary":...,"prompt_tokens":sum,"completion_tokens":sum}`.
- [ ] Commit: `feat(arena): capture local-policy transcript payload (behavior-neutral)`.
- Verify: `uv run pytest tests/arena/test_agent.py -q` → pass.

### Task 3 — CLI path: stream-json switch + **defensive** parsers (`cli_agent.py: CLIAgentPolicy`)
- [ ] RED: extend `tests/arena/test_cli_agent.py`: update argv assertion → expects `"stream-json"` and `"--verbose"`
  (not `"json"`); add a synthetic claude stream-json fixture (init → assistant `tool_use` → user `tool_result`
  → terminal `{"type":"result",...,"usage":...}`) and a codex fixture; assert (a) `_parse_claude`/`_parse_codex`
  return the **same** `(summary,in,out,usd)` as today, (b) `_stream_steps_*` recover ≥1 tool step. Expect **RED**.
- [ ] GREEN:
  - `_build_argv` claude branch: `"--output-format","json"` → `"--output-format","stream-json","--verbose"`.
  - Add static `_stream_steps_claude(stdout)` / `_stream_steps_codex(stdout)` — **defensive**: skip unparseable
    lines, never raise (wrap callers in try/except so a parser bug cannot change summary/usage or crash a turn).
    Pair claude `tool_use.id`↔`tool_result.tool_use_id`; capture codex `item.completed` items generically.
  - `__call__`: time `proc.communicate()`; attach `result["transcript"]={"steps":...,"wall_clock_s":...,
    "final_summary":summary,"cli_exit":proc.returncode,"cli_stderr_tail":...,"invalid_tool_calls":[]}`. On the
    timeout branch attach `{"steps":[],"reason":"timeout",...}`. **`summary`/`usage` and `self.cost.record(...)` unchanged.**
- [ ] Commit: `feat(arena): CLI stream-json capture (defensive parsers, provisional fixtures)`.
- Verify: `uv run pytest tests/arena/test_cli_agent.py -q` → pass. (Fixtures finalized in Task 9.)

### Task 4 — Coordinator: bootstrap-free state delta + sole writer (`coordinator.py: run_arena`)
- [ ] RED: extend `tests/arena/test_coordinator.py` with a fake `gs` whose overview query yields two snapshots;
  assert `state_before/after/delta` computed and `transcript.write` called **once per puppet turn**; with `NullSink`
  no extra game calls beyond the two reads. Expect **RED**.
- [ ] GREEN:
  - Signature: `run_arena(conn, gs, config, policy=None, policy_for=None, transcript=None)`.
  - Add a **bootstrap-free** helper (mirror game_state's `lq` import; do NOT call `gs.get_game_overview`):
    ```python
    async def _overview_snapshot(gs):
        try:
            lines = await gs.conn.execute_write(lq.build_overview_query())
            ov = lq.parse_overview_response(lines)
            return {"score":ov.score,"gold":ov.gold,"science":ov.science_yield,
                    "culture":ov.culture_yield,"faith":ov.faith,"research":ov.current_research,
                    "civic":ov.current_civic,"cities":ov.num_cities,"units":ov.num_units}
        except Exception:
            return None
    ```
  - In the puppet branch: capture `state_before` while `local==st.local` (before `conn.disconnect()`); capture
    `state_after` after `_reconnect_with_retry`, before `finish_units`. If `transcript` and `result.get("transcript")`:
    assemble the full record (merge payload + state + computed `state_delta` + run_id/turn/player/provider/model/ts;
    `provider/model` via `getattr(pol,"provider","local")` / `getattr(getattr(pol,"backend",None),"model",getattr(pol,"model",""))`)
    and `transcript.write(record)`.
- [ ] Commit: `feat(arena): coordinator writes per-turn transcript with state delta`.
- Verify: `uv run pytest tests/arena/test_coordinator.py -q` → pass.

### Task 5 — Wiring + llama.cpp default + all-backends check (`arena.py`, `config.py`, `pyproject.toml`)
- [ ] RED: extend `tests/arena/test_arena_wiring.py` — `build_policies` returns `(policies, local_backends)` where
  `local_backends` has **one entry per local spec** (assert len==2 for two local players); extend `test_config.py`
  for the new default URL + fields. Expect **RED**.
- [ ] GREEN:
  - `config.py`: `DEFAULT_GATEWAY_URL = "http://192.168.20.196:11444/v1"`; add `run_id: str = ""`,
    `transcript_dir: str = "arena_runs"`, `transcript_enabled: bool = True` to `ArenaConfig`.
  - `arena.py: build_policies`: collect every local backend; return `(policies, local_backends)`:
    ```python
    local_backends = []
    ...
    else:
        backend = OpenAICompatBackend(cfg.gateway_url, os.environ.get(cfg.api_key_env,"x"), spec.model)
        local_backends.append(backend)
        policies[spec.player_id] = LLMPolicy(backend, cost, max_steps=cfg.max_agent_steps)
    return policies, local_backends
    ```
  - `arena.py: build_args`: add `--run-id`, `--transcript-dir` (default `arena_runs`), `--no-transcript`;
    keep `--cost-path` default `""` (auto-place under run dir).
  - `arena.py: _run` (order matters):
    ```python
    from pathlib import Path
    from civ_mcp.run_id import generate_run_id
    from civ_mcp.arena.transcript import TranscriptSink, NullSink
    run_id = args.run_id or generate_run_id(model_id="arena")
    run_dir = Path(args.transcript_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)            # before CostLog (cost.py opens path directly)
    cost_path = args.cost_path or str(run_dir / "arena_cost.jsonl")
    cost = CostLog(cost_path)
    transcript = (TranscriptSink(str(run_dir / "transcript.jsonl"))
                  if not args.no_transcript else NullSink())
    policies, local_backends = build_policies(specs, cost, cfg)
    ...
    for b in local_backends:                               # check EVERY local model
        if not await b.reachable():
            raise SystemExit(f"local backend not reachable at {cfg.gateway_url} (model {b.model})")
    ...
    result = await run_arena(conn, gs, cfg, policy_for=policy_for, transcript=transcript)
    ```
  - `pyproject.toml`: add `civ-arena-analyze = "civ_mcp.arena.analyze:main"`.
- [ ] Commit: `feat(arena): llama.cpp default, run-dir, all-backends check, transcript wiring`.
- Verify: `uv run pytest tests/arena -q` → all pass.

### Task 6 — Offline analysis (`analyze.py`)
- [ ] RED: `tests/arena/test_analyze.py` — synthetic `transcript.jsonl` + `arena_cost.jsonl` fixture → assert per-model
  series, invalid-call rate, truncation-incident rate, rubric flags, and default output paths computed. Expect **RED**.
- [ ] GREEN: `src/civ_mcp/arena/analyze.py` (pure offline; entry `civ-arena-analyze`): read both files; emit Markdown+JSON:
  - CLI contract:
    ```bash
    civ-arena-analyze --run-id <run_id> \
      [--runs-dir arena_runs] \
      [--output-md arena_runs/<run_id>/report.md] \
      [--output-json arena_runs/<run_id>/report.json]
    ```
    Defaults: read `arena_runs/<run_id>/transcript.jsonl` and `arena_runs/<run_id>/arena_cost.jsonl`;
    write `arena_runs/<run_id>/report.md` and `arena_runs/<run_id>/report.json` unless output paths are supplied.
  - Series/turn: score, #cities, #units, science/culture, tokens, wall-clock, steps; `state_delta` events.
  - Rates: `invalid_tool_calls/step_count`; truncation-incident rate (steps `truncated`, locals).
  - Early-game rubric (turns 1–20): founded extra city? explored vs idle skip/fortify loops? `set_research`/
    `set_city_production` to real non-ERROR items? wasted/blind `move_unit`? hallucinated tool names?
    truncation→bad-move correlation. Output per-model table + short narrative citing concrete turns.
- [ ] Commit: `feat(arena): civ-arena-analyze offline report`.
- Verify: `uv run pytest tests/arena/test_analyze.py -q` → pass.

### Task 7 — Preflight: llama.cpp reachability + tool-calling for BOTH local models
- [ ] From `riz@192.168.20.141`: `curl http://192.168.20.196:11444/v1/models` lists `gemma4-26b` and `qwen3.6-27b`.
- [ ] **Tool-calling smoke (critical):** send a minimal OpenAI chat with one tool to each slug; confirm a well-formed
  `tool_calls` response. If a model won't emit tool calls (llama.cpp `--jinja` template gap), **STOP and report** —
  do not fall back to Ollama. (Optional cross-check: `model-fit` / `model-placement`.)
- Verify: captured JSON showing `tool_calls` for each model.

### Task 8 — Parameterize the live launcher
- [ ] Generalize `tools/skills/civ6-arena-live/scripts/start-hybrid-watch.sh` (drop the hardcoded 2-player /
  `qwen3-coder:30b`) to accept the 4 `--player` specs, `--run-id`, `--max-puppet-turns`, `--idle-poll-limit`,
  `--gateway-url`. Exact defaults:
  - `--run-id`: `hybrid-4civ-$(date -u +%Y%m%dT%H%M%SZ)`
  - `--max-puppet-turns`: `8` (2-round shake-out default)
  - `--idle-poll-limit`: `3600`
  - `--gateway-url`: `http://192.168.20.196:11444/v1`
  - if no `--player` args are supplied, use exactly:
    `1:cli-claude:`, `2:cli-codex:gpt-5.5`, `3:local:gemma4-26b`, `4:local:qwen3.6-27b`
  - if any `--player` arg is supplied, pass exactly the supplied repeated `--player` args through to `uv run civ-arena`.
  Canonical command emitted by the script's defaults (`RUN_ID` is generated inside the script):
  ```
  RUN_ID="hybrid-4civ-$(date -u +%Y%m%dT%H%M%SZ)"
  uv run civ-arena \
    --player 1:cli-claude: --player 2:cli-codex:gpt-5.5 \
    --player 3:local:gemma4-26b --player 4:local:qwen3.6-27b \
    --gateway-url http://192.168.20.196:11444/v1 \
    --max-puppet-turns 8 --idle-poll-limit 3600 --run-id "$RUN_ID"
  ```
- [ ] Commit: `chore(arena): parameterize 4-civ live launcher`.
- Verify: `--help` parses; scripts lint.

### Task 9 — Live shake-out (2 rounds) + finalize CLI parsers
- [ ] Run `tools/skills/civ6-arena-live/scripts/start-hybrid-watch.sh` with its default `--max-puppet-turns 8`.
  Capture raw `claude` stream-json and `codex --json` stdout;
  **pin them as real fixtures** and tighten `_stream_steps_claude`/`_stream_steps_codex` to actual event/`item.type`
  shapes; re-run Task 3 tests against the pinned fixtures.
- [ ] Confirm 4-way sequencing, single-tuner handoff for both CLI civs, transcript+cost written, clean human handback.
- [ ] Commit: `test(arena): pin real CLI stream fixtures; finalize parsers`.
- Verify: `arena_runs/<run_id>/transcript.jsonl` has one record per puppet turn; `civ-arena-analyze` runs clean.

### Task 10 — Full baseline run (20 rounds)
- [ ] Run the full baseline with an explicit run id:
  ```bash
  RUN_ID="hybrid-4civ-full-$(date -u +%Y%m%dT%H%M%SZ)"
  tools/skills/civ6-arena-live/scripts/start-hybrid-watch.sh \
    --run-id "$RUN_ID" \
    --max-puppet-turns 80 \
    --idle-poll-limit 3600 \
    --gateway-url http://192.168.20.196:11444/v1
  ```
  Babysit via `arena-live-status.sh`; tail `arena_runs/$RUN_ID/transcript.jsonl` for progress.
  On hang: `stop-arena-watchers.sh` (the `run_arena` `finally` reclaims tuner + restores the human seat).
- Verify: transcript covers all 4 civs across 20 intended rounds; cost log consistent.

### Task 11 — Findings report
- [ ] Run the analyzer with explicit report output paths:
  ```bash
  civ-arena-analyze --run-id "$RUN_ID" \
    --output-md "docs/devlog/civ6-arena-baseline-$RUN_ID.md" \
    --output-json "arena_runs/$RUN_ID/report.json"
  ```
  Commit the Markdown report under `docs/devlog/`. Summarize the baseline and name the
  highest-leverage local-harness fixes the data supports (map-blindness, truncation incidents, repr vs narration,
  invalid-tool hallucinations, `max_steps` exhaustion) — seeds the follow-on plan.
- [ ] Commit: `docs(arena): baseline decision-quality findings`.
- Verify: report renders; numbers reconcile with the JSONL artifacts.

---

## End-to-end verification
1. `uv run pytest tests/arena -q` → green (was 42; grows with new tests).
2. Behavior-neutrality asserted: local still fed `str(result)[:1500]`; `_parse_claude`/`_parse_codex` identical pre/post.
3. Task 7 shows `tool_calls` from both local models on `:11444` (llama.cpp).
4. Task 9 shake-out: one record per puppet turn; clean 4-way sequencing + handback.
5. Task 11 report produced from real data.

## Process notes
- Finish as an **unmerged worktree branch** — stop at commits + "ready for review"; do **not** merge/push
  without explicit direction (separate-session review).
- Memory to record after plan mode: local inference uses **llama.cpp** (llama-swap `:11444` / LiteLLM `:4000`),
  not Ollama `:11430/:11431` — feedback type.
- Carried risks: stream-json parser fragility (defensive in Task 3, fixture-pinned in Task 9); llama.cpp per-model
  tool-calling (gated by Task 7); long live duration/babysitting; per-turn overview reads (guarded, bootstrap-free).
