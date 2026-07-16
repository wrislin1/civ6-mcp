# Arena Game Master — Resident Advisory LLM & Narrative Adjudication (Design)

**Date:** 2026-07-16
**Status:** Approved by riz (written-spec review, 2026-07-16)
**Depends on:** `2026-07-09-arena-unofficial-channels-design.md`, schema version 1

## Goal

Use a second resident local model as a non-player game master while a separate resident model focuses on playing the civ seats. The master produces fresh persuasive briefings, maintains a configured personality and goals, and adjudicates otherwise-unverifiable narrative promises. It never performs a game action or overrides deterministic channel facts.

## Role Boundary

The coordinator and channels runtime remain authoritative for messages, parties, payment, deterministic term evidence, deadlines, and persistence. The master may:

- summarize a civ's situation;
- highlight obligations, opportunities, risks, and rival behavior;
- persuade a civ toward actions consistent with its configured personality/goals;
- issue a structured ruling for a supplied narrative deal.

The master may not:

- call Civ 6 or channel action tools;
- create a message, deal, payment, or grievance directly;
- alter parties, values, deadlines, or deterministic evidence;
- rule on a registered deterministic term;
- block a civ turn when unavailable.

## Architecture and File Boundaries

| File | Responsibility |
|---|---|
| `src/civ_mcp/arena/config.py` | Top-level `MasterOptions`, validation inputs, and fingerprint material |
| `src/civ_mcp/arena/master.py` | Typed input/submission/ruling records, prompt construction, output validation, privacy-safe formatting |
| `src/civ_mcp/arena/master_runtime.py` | One resident backend, per-mode memory, invocation/idempotency, adjudication routing, transcripts |
| `src/civ_mcp/arena/arena.py` | Construct and preflight the master backend independently of civ policies |
| `src/civ_mcp/arena/backends.py` | Backward-compatible forced-tool and per-role timeout controls used by the master call |
| `src/civ_mcp/arena/cost.py` | Preserve player costs while recording the separate `game_master` role |
| `src/civ_mcp/arena/coordinator.py` | Call the runtime at the defined admission point and pass only `master_block` to the mover |
| `src/civ_mcp/arena/prompting.py` | Place the optional master block in the fixed opening order |
| `src/civ_mcp/arena/analyze.py` | Separate master cost, failure, ruling, and contamination summaries |

`master_runtime.py` owns the integration. The coordinator does not parse master output, mutate memory, or manufacture rulings.

## Configuration

The master is a top-level non-player spec:

```yaml
master:
  mode: private_adviser
  provider: local
  model: master-model-alias
  gateway: http://gpu2-endpoint/v1
  personality: "Patient, theatrical, and fascinated by shifting alliances."
  goals:
    - "Create consequential diplomatic choices."
    - "Prevent one civilization from becoming unchallenged too early."
  max_briefing_chars: 1200
  memory_chars: 4000
  timeout_s: 60
  adjudication_grace_turns: 2
```

`mode` is `off`, `private_adviser`, or `director`. Active modes require `provider: local`, a non-blank model/gateway, personality, and at least one goal. `adjudication_grace_turns` defaults to 2 and is bounded to 0–3 game turns. The complete normalized spec is included in the experiment/run fingerprint and every master transcript record.

The configured alias/gateway routes the master to GPU 2; civ policies remain on the GPU 1 endpoint. The arena builds one `OpenAICompatBackend` for the master at startup and keeps both models resident. Calls are intentionally sequential, not speculative: fresh master inference completes before mover inference begins.

## Modes and Visibility

### Off

No master backend is built, no master state is loaded, and core channels accept deterministic terms only.

### Private Adviser (Default Active Mode)

For target player `p`, the master receives:

- the deterministic game briefing already built for `p`;
- `project_for_player(p)` from the channel runtime when channels are enabled, otherwise an explicit empty channel projection;
- `p`'s isolated master memory;
- configured personality and goals;
- narrative adjudication requests involving `p` only, with bilateral messages and privacy-safe observation evidence.

It never receives another player's private projection or memory. One canary belonging only to player A must be absent from player B's master prompt, raw response, memory, mover block, and transcript projection.

### Director

The director receives the target civ's game briefing plus:

- full channel projection;
- a global world snapshot;
- one global director memory;
- configured personality and goals;
- all due narrative adjudications.

It still returns a separate briefing for only the current target civ, but it may intentionally manipulate that civ using global knowledge. Starting or accessing director mode sets `privacy_contaminated=true` in run metadata, transcript analysis, and human projections. Director runs are never silently pooled with clean bilateral experiments.

## Invocation Flow

The master runs only when a player will actually act. Channel participation is not required:

1. Coordinator admits a civ and completes deterministic pre-model tasks/attention evaluation.
2. If an automated civ sleeps, no master call occurs.
3. Coordinator reuses the civ briefing, channel projection, and due narrative-ruling requests to build `MasterInput`.
4. Master returns one structured tool call.
5. Coordinator validates, clamps, persists, and formats the result.
6. Mover receives the same deterministic context plus `master_block`.

API and CLI seats invoke the master immediately before mover inference. Once the human-surface slice is installed, a human-pending seat invokes it once when that player-turn first becomes local, before publishing the handoff projection; repeated web polls do not trigger additional calls.

The fixed mover block order is game briefing, standing memory, task tracker, channel block, master block, attention digest, blocker repair, turn announcement/capture instructions.

## Structured Output

The master receives no action tools. It receives one submission schema:

```json
{
  "briefing": "A persuasive, target-specific advisory briefing.",
  "memory_update": "Bounded persistent notes for this master namespace.",
  "rulings": [
    {
      "deal_id": "deal-000031",
      "verdict": "broken",
      "reason": "The promised defensive screen was never credible.",
      "evidence_refs": ["obs-000912", "msg-000441"],
      "confidence": 0.82
    }
  ]
}
```

`verdict` is `honored`, `broken`, or `unverifiable`; confidence is 0–1. Wronged and offending parties are derived from the immutable deal rather than supplied by the model. The runtime rejects unknown deal IDs, deterministic terms, unavailable evidence references, duplicate rulings, and output beyond configured bounds. Invalid rulings do not invalidate an otherwise valid briefing.

The mover-facing `master_block` contains only the validated briefing. Memory updates remain private master state, and rulings go directly to the deterministic channel runtime; neither is copied into mover instructions.

## Narrative Terms and Grievances

Active master modes permit:

```json
{
  "term_type": "narrative",
  "params": {
    "text": "Maintain a credible defensive force near our shared enemy without threatening my border"
  }
}
```

Payment, proposal acceptance, and timing remain deterministic. At the narrative deadline, the runtime assigns the ruling once to the proposer/requester's next master call. Channel attention wakes that player if necessary. The runtime supplies the accepted text, bilateral conversation, relevant persisted observations, legal party IDs, and exact deadline as a ruling request. This fixed assignment prevents duplicate or turn-order-dependent rulings while remaining within the deal's bilateral privacy scope.

A validated `broken` ruling produces a normal unofficial grievance with:

- `adjudication_source: game_master`;
- master mode/model;
- configured prompt hash;
- raw structured ruling and evidence references;
- the same magnitude/decay rules as deterministic grievances.

Registered terms always use `adjudication_source: deterministic` and never enter the master queue.

If the master is unavailable or returns no valid ruling, adjudication remains pending through `adjudication_grace_turns` and is retried on the assigned player's admitted turns. Expiry of that grace ends the deal as `unverifiable`; no grievance is created. This never blocks a game turn.

## Memory

- Private mode: `channels/master/private/player_<id>.json`
- Director mode: `channels/master/director.json`

Memory files are schema-versioned, atomically replaced, bounded by `memory_chars`, and fingerprinted to master mode/model/personality/goals. Switching those inputs under the same run ID fails closed rather than reusing incompatible memory.

Private memory can be loaded only for its matching player. Director memory is never injected into a private-adviser call.

## World Context

Private mode sees exactly the target civ's existing deterministic briefing. Director mode adds one bounded `MasterWorldSnapshot` containing global turn/score/yield/military/city/victory summaries and the full channel projection. It does not receive arbitrary raw Lua, filesystem access, or game tools.

The global snapshot is persisted with the master transcript so a ruling can be audited against what the model actually saw.

## Failure, Cost, and Transcripts

- Reachability is preflighted at startup, but a later outage is fail-open.
- Timeout, backend error, malformed output, or rejected output yields an empty master block and explicit record.
- Mover inference is never retried merely because the master failed.
- Cost records use role `game_master` and remain separate from civ-player costs.
- Each call records mode, target, model/gateway identity, prompt/context hashes, visible projection IDs, latency, usage, parsed output, validation errors, memory chars, and contamination state.

## Testing and Live Gate

Offline tests cover:

- config validation/fingerprint and one resident backend construction;
- private prompt/memory canary isolation;
- director visibility and contamination marking;
- fixed prompt ordering for API and CLI movers;
- one-call structured response validation and independent ruling rejection;
- narrative ruling lifecycle and tagged grievance;
- deterministic-term exclusion;
- timeout/backend/malformed-output fail-open behavior;
- memory bounds, mode separation, and incompatible-resume rejection;
- attention sleep skips master inference; channel wake events trigger it;
- channel-disabled movers receive isolated briefings with an empty channel projection;
- human handoff invokes once per player-turn rather than once per web poll;
- master cost/transcript fields and analysis aggregation.

The attended two-GPU gate verifies:

1. master alias remains resident/routed on GPU 2 and mover alias on GPU 1;
2. a private-adviser canary cannot cross players;
3. a narrative promise produces a tagged ruling/grievance;
4. director mode produces persuasive target-specific briefings and marks contamination;
5. stopping the master endpoint causes movers to continue with deterministic context only.

## Non-Goals

- No concurrent/speculative inference or stale-brief invalidation.
- No master game/channel action tools.
- No direct master-authored messages, resources, or commands.
- No override of deterministic facts or payments.
- No claim that director-mode runs preserve bilateral experimental privacy.
- No web UI; the human surface is the next slice.
