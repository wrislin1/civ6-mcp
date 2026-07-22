# Arena Channels Behavior v1 — First LLM Channel-Uptake Experiment

**Goal:** Run the first real (non-deterministic) channels-enabled arena
experiment: two local LLM agents with unofficial channels enabled, measured on
whether and how they negotiate, honor, and break private deals.

**Status:** designed 2026-07-22, after `arena-channels-core-gate-v4` proved the
channel machinery live (terminal PASS, evidence in
`docs/superpowers/plans/2026-07-16-arena-unofficial-channels-core-live-gate.md`).

## Background and motivating evidence

- **2026-07-16 null result:** the only prior real-agent channels run
  (`arena-channels-core-smoke`, gemma4-26b + cli-codex, one puppet turn each)
  produced zero channel actions. Gemma hit `max_steps reached` (default 6) on
  ordinary game actions; codex played a full coherent turn and ignored the
  channel surface entirely.
- **Thinking-mode finding (2026-07-22, live-probed):** the deployed
  `gemma4-26b` and `qwen3.6-27b` chat templates think by default, emitting
  `reasoning_content` that consumes the output budget before any content
  (qwen burned a 60-token budget entirely on reasoning and returned empty
  content, finish `length`; gemma spent 50 tokens of reasoning to say "OK").
  The arena's `OpenAICompatBackend` never disables thinking, so every local
  arena seat to date ran with it on. First diagnosed in living-emerald
  (2026-07-10, `orchestrator/llm.py`); fix is
  `chat_template_kwargs: {"enable_thinking": false}` — verified today to cut
  both models to 2-token direct answers.
- **GPU topology (2026-07-22, live-verified):** 192.168.20.196 has two RTX
  3090s behind llama-swap profiles: `:11440` = GPU0-pinned, `:11441` =
  GPU1-pinned, `:11444` = unified split across both cards. `gemma4-26b` on
  `:11440` and `qwen3.6-27b` on `:11441` served concurrently — cross-family
  roster with zero model-swap overhead.

## Hypothesis

With the three identified obstacles removed — thinking-mode token burn,
6-step turn budget, and zero prompting about why channels matter — local LLM
agents will use the channel surface. The experimental frame is **encouraged
negotiation** (option B of the design discussion): a modest guidance nudge is
added and documented as an experimental variable; no behavior is scripted.

**Success criteria:**

- Primary (uptake): ≥1 channel action dispatched per LLM seat over the run.
- Secondary (lifecycle): ≥1 deal proposed; record how far its lifecycle
  progresses (accept → fund → payment response → favor honored/broken).
- Qualitative: message and deal content from transcripts — what do the agents
  ask for, offer, and how do they react to grievances?

A null result is informative: with all three obstacles removed, continued
silence means the channel surface itself (placement, rendering, salience)
needs redesign, not the agents' budgets.

## Roster and infrastructure

| Seat | Provider | Model | Gateway | Notes |
|---|---|---|---|---|
| Player 1 | local | `gemma4-26b` | `http://192.168.20.196:11440/v1` (GPU0) | channels + guidance on |
| Player 2 | local | `qwen3.6-27b` | `http://192.168.20.196:11441/v1` (GPU1) | channels + guidance on |
| Player 3 | scripted | — | — | channels enabled, silent control seat |

- Both LLM seats: `max_steps: 10` (up from default 6), `tools: minimal`.
- Player 3 answers the free control question: does anyone message a player
  who never responds?
- Save: `CHANNELS_GATE_V1_T157` (turn 157, human player 0, players 1–3 alive;
  official trades between players 1 and 2 proven legal by gate-v4).
- Run cost: $0 cloud; both models local, one per GPU, no swapping.
- GPU0 also hosts ComfyUI/Halo/Herald/Effigy — run-day preflight must confirm
  the 26B model actually loads and generates on `:11440`.

## Experiment config

New file `experiments/arena-channels-behavior-v1.yaml` (the gate config
`arena-channels-core-smoke.yaml` is not modified):

- `run_id: arena-channels-behavior-v1`
- `max_puppet_turns: 30`, `max_game_turns: 12` — 10 game rounds of three
  seats, plus slack for watcher handoff.
- `channel_rules: {acceptance_turns: 3, funding_turns: 2,
  payment_response_turns: 2}` — the live-proven gate deadlines.
- **No `live_gate` block** — this is an ordinary channels run. The rev-3
  ordinary-run settlement verification in `_fund_deal` (treasury-delta check,
  `Error: CHANNEL_PAYMENT_NOT_ENACTED` on verifiable non-enactment) is the
  active safety net for real payments.
- Per-civ: `channels: {enabled: true, guidance: true}` for players 1–2;
  player 3 `channels: {enabled: true}` only.

## Code changes

Two small code changes plus the experiment config, all independently testable:

### 1. Backend: disable thinking unconditionally

`OpenAICompatBackend.chat` (`src/civ_mcp/arena/backends.py`) adds
`extra_body={"chat_template_kwargs": {"enable_thinking": False}}` to every
chat-completions request.

- Mirrors living-emerald's resolution verbatim; a harmless no-op for
  non-thinking models and for endpoints that ignore unknown template kwargs.
- No config knob (YAGNI): the first experiment that wants thinking on adds
  the knob then. CLI providers (`cli-claude`, `cli-codex`) do not use this
  backend and are unaffected.
- Preserve the existing request contract: `max_tokens`, `timeout`, optional
  `tools`/`tool_choice`, bounded non-timeout retries, and immediate timeout
  re-raise stay unchanged. Backend tests assert the new `extra_body` is present
  in both tool and no-tool calls.

### 2. Channels: per-player guidance preamble

`ChannelOptions` (`src/civ_mcp/arena/config.py`) gains
`guidance: bool = False`, parseable from the experiment YAML's per-civ
`channels:` mapping. `_parse_channels` accepts exactly `enabled` and
`guidance`; both values must be exact booleans. `CivOptions.fingerprint()`
includes `{"enabled": ..., "guidance": ...}` so transcripts distinguish this
guided experiment from an otherwise identical unguided run. The lower-level
`channel_config_fingerprint()` and canonical `ChannelState.rules_fingerprint`
do not include guidance, because guidance is prompt furniture rather than
ledger identity.

Rendering contract:

- `ChannelProjection` (`src/civ_mcp/arena/channels.py`) gains
  `guidance: bool = False`.
- `ChannelRuntime.admit_player(gs, player_id, turn, *, guidance: bool = False)`
  sets the returned projection's `guidance` flag before formatting the block.
  Existing callers that omit the keyword preserve current behavior.
- The coordinator passes `spec.options.channels.guidance` for the admitted
  actor when channels are enabled.
- `format_channel_block(projection)` keeps its existing signature and inserts
  the static guidance paragraph immediately after the
  `== PRIVATE UNOFFICIAL CHANNELS ==` header when `projection.guidance` is
  true.
- No guidance text is written to canonical `channels/state.json` or
  `channels/events.jsonl`.

Exact guidance text (an experimental variable — changing it is a new
experiment revision):

> These channels are private back-channel negotiations with rival leaders —
> invisible to everyone else. You can send private messages, propose deals
> that trade gold for in-game favors (for example destroying a barbarian
> camp or keeping units out of an area), accept or decline offers, fund
> payments, and acknowledge payments received. Deals are NOT enforced by the
> game: a promise can be honored or broken. Breaking a promise creates a
> lasting grievance the wronged player remembers. Used well, deals can earn
> you gold, remove threats, or buy cooperation you cannot get openly. Review
> the channel state below every turn and weigh whether a message or deal
> would advance your position.

The nudge is encouraging, not directive: it never instructs the agent to act
this turn. The observable contract is: guidance text present in the prompts of
players configured `guidance: true`, absent for everyone else, and absent from
all persisted canonical channel state.

Tests:

- `tests/arena/test_experiment.py` loads
  `channels: {enabled: true, guidance: true}` and rejects non-boolean
  `channels.guidance`.
- `tests/arena/test_config.py` asserts channel defaults and
  `CivOptions.fingerprint()["channels"] == {"enabled": False, "guidance":
  False}`.
- `tests/arena/test_channels.py` asserts the exact guidance paragraph appears
  immediately after the header only when `ChannelProjection.guidance` is true,
  and that the unguided block remains unchanged apart from the projection
  dataclass gaining a defaulted field.
- `tests/arena/test_channel_runtime.py` asserts the admission keyword sets the
  projection flag and rendered block.
- `tests/arena/test_coordinator.py` asserts the coordinator passes guidance
  only for configured players.

### 3. Experiment config artifact

`experiments/arena-channels-behavior-v1.yaml` is committed with the roster and
rules above. A loader test asserts:

- run ID is `arena-channels-behavior-v1`;
- no `live_gate` block is enabled;
- players 1 and 2 are local, `tools: minimal`, `max_steps: 10`,
  channels-enabled, and `guidance: true`;
- player 3 is scripted, channels-enabled, and `guidance: false`;
- channel deadlines are acceptance 3, funding 2, payment response 2.

## Measurement

No analyzer changes (YAGNI). The existing channels report already provides:
message counts per player/pair, deal counts, payment states
(settled/failed/…), outcomes (honored/broken/declined/expired), grievances
with magnitudes and adjudication sources. Qualitative analysis reads
`transcript.jsonl` and the canonical `channels/events.jsonl`.

## Run procedure (attended)

1. Operator manually loads `CHANNELS_GATE_V1_T157` (main-menu load; no
   automation exists at the menu on this rig).
2. Preflight: FireTuner ownership map clean (stop any stale `civ-mcp`);
   both gateways answer a tiny thinking-off generation on their pinned
   models; `arena_runs/arena-channels-behavior-v1` does not exist.
3. Arm exactly one watcher:
   `setsid uv run civ-arena --config experiments/arena-channels-behavior-v1.yaml`
   (detached, output under `.arena-runs/`), default idle-poll override 1800.
4. Operator ends turns as control returns; no restart handshake exists in
   ordinary runs — one continuous watcher until 30 puppet turns, budget, or
   idle limit.
5. After exit: run `civ-arena-analyze`, read the channels report and
   transcripts, write up findings.

Estimated attended time 1–2 hours.

## Risks and handling

- **GPU0 contention:** preflight generation on `:11440` proves VRAM is free;
  a 502 (`upstream command exited prematurely`) means GPU0 services must be
  paused first.
- **Malformed channel tool calls from 26B models:** acknowledged as data —
  channel dispatch rejects invalid actions with an acknowledgement the agent
  sees next turn; nothing crashes.
- **A seat wedges or the gateway stalls:** the coordinator's existing degrade
  guard skips that seat's turn; the backend's 300 s timeout bounds runaway
  generation (much less likely with thinking off).
- **Payment enactment failure:** rev-3's ordinary-run fund verification fails
  closed with `Error: CHANNEL_PAYMENT_NOT_ENACTED`; the ledger keeps funding
  due rather than fabricating settlement.

## Out of scope (explicit)

- Cross-family vs self-play A/B, strong-vs-weak asymmetry, channels-on vs
  channels-off strategic comparison — follow-up experiments.
- CLI-provider seats (cost; local-first direction).
- Analyzer extensions, briefing-section integration of channel state, or any
  change to gate scenario behavior (`SCENARIO_REVISION` stays 3).
- A thinking-mode config knob.
