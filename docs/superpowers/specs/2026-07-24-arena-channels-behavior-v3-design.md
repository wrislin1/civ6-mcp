# arena-channels-behavior-v3 — Design

**Date:** 2026-07-24 · **Status:** approved (design presented and accepted
in session)

## Context

v1 (zero engagement), v1b (step headroom → messages), and v2 (30 game
turns → grounded free-text negotiation, still zero deal objects) are
recorded in `docs/research/arena-channels-behavior-v1-findings.md`. The
models demonstrably want to trade gold for services but never touch the
deal machinery: in v2 the channel ledger shows 5 applied `send_message`
actions and zero attempted deal actions — not even an invalid one.

v3 applies both remaining levers as a combined treatment (user decision:
faster answer now, ablate later if needed):

1. **Directive guidance** — the guidance paragraph names the deal actions
   and states that message text is not binding.
2. **Scripted opener** — the scripted seat opens the run by proposing a
   formal deal to each LLM seat, so both models must engage the deal
   lifecycle (`respond_to_deal`) rather than chat.

## Success criteria

- **Primary:** each LLM seat issues ≥1 `respond_to_deal` (accept or
  decline — either is engagement).
- **Secondary:** at least one deal reaches a terminal honored/settled
  state (full lifecycle: accept → favor window → fund → payment response).
- **Stretch:** any LLM-initiated `propose_deal`.

## Lever 1: directive guidance

`CHANNEL_GUIDANCE_TEXT` (`src/civ_mcp/arena/channels.py:15`) gains a
directive closing. The constant becomes the existing text plus, appended
as additional sentences (exact copy, one string concatenation continuing
the current style):

> " Important: messages alone are NOT binding — a deal exists only once
> it is created with the propose_deal action and answered with
> respond_to_deal. When you and a rival converge on terms, turn them into
> a propose_deal immediately; when a proposal is pending for you, answer
> it with respond_to_deal before it expires; fund deals you owe with
> fund_deal."

No new config: the same `guidance: true` flag renders the revised text.
The byte-pinned guidance test is updated to the new constant. Historical
runs remain interpretable via the commit recorded in findings.

## Lever 2: scripted channel actions

### Config (`config.py`, `experiment.py`)

`ChannelOptions` gains `script: tuple[ChannelScriptStep, ...] = ()` where
`ChannelScriptStep` is a frozen dataclass `{turn: int, action: str,
args: dict}`. YAML shape:

```yaml
channels:
  enabled: true
  script:
    - turn: 157
      action: propose_deal
      args: {...}
```

Parse-time validation (in `_parse_channels`):
- `script` optional; when present must be a list of mappings with exactly
  the keys `{turn, action, args}`.
- `turn`: int ≥ 1 (strict — booleans rejected, matching the existing
  strict-bool discipline).
- `action`: one of the channel tool names from
  `channel_tool_schemas()` (`send_message`, `propose_deal`,
  `respond_to_deal`, `fund_deal`, `respond_to_payment`).
- `args`: a mapping; deep validation is NOT duplicated at parse time —
  the runtime dispatcher (`ChannelTurnContext.dispatch`) already
  validates and folds rejections into acknowledgements.
- `script` on a spec whose `channels.enabled` is false is a parse error.

Fingerprint treatment mirrors `guidance`: `script` appears in
`CivOptions.fingerprint()["channels"]` (transcript metadata) and is
excluded from `channel_config_fingerprint()` and
`ChannelState.rules_fingerprint` — journal identity is untouched, and the
scripted actions themselves are journaled as ordinary staged-action
events.

### ScriptedPolicy (`scripted_policy.py`)

`ScriptedPolicy.__call__` gains channel awareness. The coordinator
already passes `channel_context` (the `ChannelTurnContext`) and
`channel_block` to any policy whose signature accepts them
(`coordinator.py:1841`, gated by `_policy_accepts_kwarg`); the
coordinator additionally passes `channel_projection`
(`ChannelAdmission.projection`) at the same site so a policy can read
deal state. LLM policies do not accept the new kwarg; only
`ScriptedPolicy` declares it.

On each NORMAL call with a non-None `channel_context`, after the existing
observe/skip behavior:

1. **Script steps:** dispatch every `script` entry whose `turn` equals
   the current turn, in list order, via `channel_context.dispatch(action,
   args)`.
2. **Auto-fund:** for each deal in the projection where this player is
   the proposer, the deal is accepted/active, and the payment is due for
   funding, dispatch `fund_deal({"deal_id": ...})`. This is a standing
   deterministic rule, not a script entry, because the deal id is not
   knowable when the yaml is written.

Each dispatch outcome (or exception, caught per-dispatch) is appended to
the returned summary/actions — the policy never raises, matching its
existing error discipline. Repair calls (`blocker_block` non-empty) do
not run channel logic.

### v3 artifact (`experiments/arena-channels-behavior-v3.yaml`)

v2 baseline (90 puppet turns, `max_game_turns` 108, 15 steps, guidance on
both LLM seats, same gateways/models) plus P3's opener script — one
`propose_deal` to each LLM seat on P3's first turn:

```yaml
run_id: arena-channels-behavior-v3
max_puppet_turns: 90
max_game_turns: 108
channel_rules:
  acceptance_turns: 3
  funding_turns: 2
  payment_response_turns: 2
civs:
  - player: 1
    provider: local
    model: gemma4-26b
    gateway: http://192.168.20.196:11440/v1
    tools: minimal
    max_steps: 15
    channels: {enabled: true, guidance: true}
  - player: 2
    provider: local
    model: qwen3.6-27b
    gateway: http://192.168.20.196:11441/v1
    tools: minimal
    max_steps: 15
    channels: {enabled: true, guidance: true}
  - player: 3
    provider: scripted
    channels:
      enabled: true
      script:
        - turn: 157
          action: propose_deal
          args:
            to_player: 1
            text: "I'll pay you 50 gold if you keep your military units
              at least 3 tiles away from my lands for the next few turns.
              A simple deal to build trust between us."
            favor:
              term_type: keep_units_away
              params: {player_id: 3, min_distance: 3, unit_scope: military}
            payment_gold: 50
            timing: on_delivery
            within: 5
        - turn: 157
          action: propose_deal
          args:
            to_player: 2
            text: "I'll pay you 50 gold if you keep your military units
              at least 3 tiles away from my lands for the next few turns.
              A simple deal to build trust between us."
            favor:
              term_type: keep_units_away
              params: {player_id: 3, min_distance: 3, unit_scope: military}
            payment_gold: 50
            timing: on_delivery
            within: 5
```

Term rationale: `keep_units_away` (params `{player_id, min_distance,
unit_scope}` per `_validate_unit_distance`, `channel_terms.py:380`) with
`player_id: 3` obligates the LLM seat to keep military units ≥3 tiles
from P3 — trivially honorable (both LLM civs sit far from P3 and stayed
home in v1b/v2), continuously verifiable from unit observations, and the
5-turn window lets the full lifecycle (accept by +3, favor window, fund
within 2, payment response within 2) finish inside the first third of the
run. `payment_gold: 50` is small enough that the P3 AI civ's treasury can
fund it.

## Run procedure (attended)

Identical to v1b/v2: operator loads `CHANNELS_GATE_V1_T157`; preflight
(FireTuner slot free, both gateways answer a thinking-off generation, run
dir absent); arm one detached watcher with the v3 config and idle-poll
override 1800; operator ends turns; afterwards `civ-arena-analyze` +
findings update.

## Testing

- `test_channels.py`: guidance byte-pin updated to the revised constant.
- `test_config.py` / `test_experiment.py`: script parsing — happy path,
  unknown action rejected, bad turn rejected, extra keys rejected, script
  without `enabled: true` rejected; fingerprint includes `script`,
  channel-rules fingerprints unchanged; v3 loader test pinning the
  artifact (including both script steps).
- `test_scripted_policy.py` (or the existing scripted-policy test home):
  script step dispatched on matching turn only; multiple same-turn steps
  dispatched in order; auto-fund dispatches `fund_deal` for exactly the
  proposer's accepted+due deals; dispatch exceptions folded into summary,
  never raised; repair calls skip channel logic.
- `test_coordinator.py`: `channel_projection` kwarg passed to policies
  that accept it, absent for those that don't.

## Out of scope

- No analyzer changes (deal tallies already reported).
- No live_gate involvement.
- No conditional/branching script steps (turn-matched linear list +
  auto-fund only — YAGNI until an experiment needs more).
- No change to LLM-side step budgets, models, or turn budgets (held at
  the v2 baseline so the two levers are the only treatment).
