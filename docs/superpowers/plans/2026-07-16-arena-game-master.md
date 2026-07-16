# Arena Game Master Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one resident non-player local model that produces fresh privacy-scoped persuasive briefings and adjudicates narrative promises while a separate resident model plays civ turns.

**Architecture:** `MasterRuntime` owns one backend, structured submission validation, mode-scoped memory, call idempotency, transcripts, and routing of validated narrative rulings into the authoritative `ChannelRuntime`. The coordinator builds one deterministic mover context, invokes the master sequentially, and passes only the validated briefing to the mover. Private adviser mode receives typed per-player projections; director mode receives an explicit global snapshot and permanently contaminates run privacy metadata.

**Tech Stack:** Python 3.12, OpenAI-compatible local backend, forced function-tool submission, frozen dataclasses, JSON/JSONL owner-only persistence, pytest/pytest-asyncio, two llama.cpp endpoints on separate RTX 3090s.

**Specs:** Read `docs/superpowers/specs/2026-07-16-arena-game-master-design.md` and the approved core spec before Task 1.

## Global Constraints

- Begin only from the reviewed tip of `arena-unofficial-channels-core`; schema version remains exactly `1`.
- Master modes are exactly `off`, `private_adviser`, and `director`; `private_adviser` is the default active mode when the block is configured.
- Active modes require `provider: local`, non-blank model/gateway/personality, and at least one non-blank goal configured at run start.
- Defaults/bounds are: briefing 1,200 characters, memory 4,000 characters, timeout 60 seconds, adjudication grace 2 turns bounded 0–3.
- The normalized full master config is included in the experiment fingerprint, memory fingerprint, and every master transcript record.
- There is one master backend object for the run. Master and mover calls are sequential: fresh master completion first, mover inference second; never overlap/speculate.
- The master receives no game or channel action tools. It receives only the forced `submit_master_briefing` schema.
- The mover sees only `master_block` containing validated briefing prose. It never sees master memory updates, raw rulings, validation errors, global director state, or other players' projections.
- Deterministic terms and payment state never enter master adjudication and cannot be overridden. Only accepted `term_type: narrative` deals are eligible.
- A validated broken narrative ruling creates the normal unofficial grievance with `adjudication_source="game_master"`; wronged/offender are derived from the immutable deal.
- Private adviser prompts/memory/transcripts are player-isolated. Director startup or access permanently sets `privacy_contaminated=true`.
- Timeout, backend error, malformed response, invalid ruling, or unavailable master fails open with an empty master block and an explicit transcript record; mover inference is not retried.
- Master runs only when a player will act. Automated sleep skips it; channel wake events can cause it. A human handoff call key executes once per player-turn regardless of web polls.
- Existing player cost records remain backward-compatible; master records add `role="game_master"` and are summarized separately.
- Run tests as `uv run pytest tests/ -q` and preserve `BaseException` cancellation/seat cleanup.
- End state is an unmerged local branch `arena-game-master`, based on the reviewed core tip in a worktree created via `superpowers:using-git-worktrees`. Do not push or merge without riz's direction.

## Stable Interfaces

`src/civ_mcp/arena/config.py` exports:

```python
@dataclass(frozen=True)
class MasterOptions:
    mode: str = "off"
    provider: str = ""
    model: str = ""
    gateway: str = ""
    personality: str = ""
    goals: tuple[str, ...] = ()
    max_briefing_chars: int = 1200
    memory_chars: int = 4000
    timeout_s: float = 60.0
    adjudication_grace_turns: int = 2

    @property
    def active(self) -> bool: ...
    def fingerprint(self) -> dict: ...
```

`src/civ_mcp/arena/master.py` exports `MasterRuling`, `MasterSubmission`, `NarrativeRulingRequest` (a re-export of the core `channels.NarrativeAdjudicationRequest`), `MasterWorldSnapshot`, `MasterInput`, `ValidatedMasterOutput`, `MASTER_TOOL_SCHEMA`, `build_master_messages`, `validate_master_reply`, and `format_master_block`. Keeping the request record in `channels.py` prevents a channel-runtime → master-runtime import cycle.

`src/civ_mcp/arena/master_runtime.py` exports:

```python
@dataclass(frozen=True)
class MasterCallResult:
    block: str
    briefing: str
    call_key: str
    status: str
    transcript: dict


class MasterRuntime:
    @classmethod
    def open(cls, run_dir: Path, options: MasterOptions, backend: Any,
             cost: CostLog, channels: ChannelRuntime | None) -> "MasterRuntime": ...
    async def invoke(self, *, target_player: int, turn: int, briefing: Briefing,
                     channel_projection: ChannelProjection,
                     gs: Any | None = None, handoff: bool = False) -> MasterCallResult: ...
    def latest_briefing(self, player_id: int) -> str: ...
```

The core `ChannelRuntime` gains `set_narrative_adjudicator`, `due_narrative_requests`, `apply_master_rulings`, and `mark_privacy_contaminated`; these are the only master-to-channel mutation paths.

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/civ_mcp/arena/master.py` | Create | Typed inputs/outputs, prompt, forced schema, validation, privacy-safe formatter |
| `src/civ_mcp/arena/master_runtime.py` | Create | Backend invocation, memory/state, idempotency, transcripts, context routing |
| `src/civ_mcp/arena/config.py`, `src/civ_mcp/arena/experiment.py` | Modify | `MasterOptions`, strict YAML, fingerprint/validation |
| `src/civ_mcp/arena/channels.py`, `src/civ_mcp/arena/channel_protocol.py`, `src/civ_mcp/arena/channel_runtime.py` | Modify | Narrative term activation, due assignment, rulings, tagged grievances, contamination |
| `src/civ_mcp/arena/backends.py` | Modify | Optional forced tool choice and per-call timeout without changing mover defaults |
| `src/civ_mcp/arena/cost.py` | Modify | Separate `game_master` role records/summary |
| `src/civ_mcp/arena/arena.py` | Modify | Pre-open the single channel runtime when needed, build/preflight one master backend/runtime, and inject both into the coordinator |
| `src/civ_mcp/arena/coordinator.py` | Modify | Shared deterministic briefing, sequential master call, mover block, transcript linkage |
| `src/civ_mcp/arena/agent.py`, `src/civ_mcp/arena/cli_agent.py`, `src/civ_mcp/arena/prompting.py` | Modify | Optional `master_block` injection already reserved by core, metadata regressions |
| `src/civ_mcp/arena/analyze.py` | Modify | Master cost/failure/ruling/contamination summaries |
| `tests/arena/test_master.py` | Create | Prompt/output validation and privacy checks |
| `tests/arena/test_master_runtime.py` | Create | Memory, backend, idempotency, modes, fail-open |
| Existing arena tests | Modify | Config, core narrative, backend, cost, arena, coordinator, policies, analysis |
| `experiments/arena-master-private-smoke.yaml` | Create | GPU-0 mover + GPU-1 private adviser gate |
| `experiments/arena-master-director-smoke.yaml` | Create | Explicit director/contamination gate |
| `docs/superpowers/plans/2026-07-16-arena-game-master-live-gate.md` | Create | Exact two-GPU attended evidence checklist |

## Dependency Order and Spec Coverage

Tasks 1–3 establish configuration, validated structured output, and one resident runtime. Task 4 extends only the narrative channel seam. Task 5 implements private/director visibility and contamination. Task 6 wires backend/cost construction. Task 7 adds sequential coordinator invocation. Tasks 8–9 complete analysis, failure coverage, and the live gate.

| Approved design area | Task |
|---|---|
| Run-start personality/goals/config/fingerprint | 1 |
| Forced one-call output, independent ruling rejection | 2 |
| One backend, memory bounds/mode separation/idempotency/transcripts | 3 |
| Narrative lifecycle, fixed assignment/grace, tagged grievances | 4 |
| Private canaries, director world/full projection/contamination | 5 |
| Separate residency, timeout, cost role, startup preflight | 6 |
| Sleep/wake, shared briefing, sequential mover injection, human handoff key | 7 |
| Fail-open, analysis, two-GPU attended proof | 8–9 |

---

### Task 1: Master configuration, strict YAML, and experiment fingerprint

**Files:**
- Modify: `src/civ_mcp/arena/config.py`
- Modify: `src/civ_mcp/arena/experiment.py`
- Modify: `tests/arena/test_config.py`
- Modify: `tests/arena/test_experiment.py`

**Interfaces:**
- Consumes: `ArenaConfig`, existing strict mapping helpers, channel config fingerprint.
- Produces: `MasterOptions`, `ArenaConfig.master`, master validation and normalized fingerprint.

- [ ] **Step 1: Add failing option/fingerprint tests**

Append:

```python
from civ_mcp.arena.config import MasterOptions, arena_experiment_fingerprint


def test_master_defaults_off_and_has_stable_fingerprint():
    opts = MasterOptions()
    assert opts.active is False
    assert opts.fingerprint() == {
        "mode": "off", "provider": "", "model": "", "gateway": "",
        "personality": "", "goals": [], "max_briefing_chars": 1200,
        "memory_chars": 4000, "timeout_s": 60.0,
        "adjudication_grace_turns": 2,
    }


def test_master_personality_and_goals_change_run_fingerprint():
    base = ArenaConfig(players=[])
    active = MasterOptions(
        mode="private_adviser", provider="local", model="qwen3.6-27b",
        gateway="http://192.168.20.196:11441/v1", personality="Theatrical",
        goals=("Create hard choices",),
    )
    assert arena_experiment_fingerprint(replace(base, master=active)) != arena_experiment_fingerprint(base)
```

- [ ] **Step 2: Add failing YAML validation cases**

```python
@pytest.mark.parametrize("master, match", [
    ({"mode": "private_adviser"}, "active master requires provider 'local'"),
    ({"mode": "director", "provider": "local", "model": "m", "gateway": "http://g/v1",
      "personality": "p", "goals": []}, "at least one goal"),
    ({"mode": "private_adviser", "provider": "local", "model": "m", "gateway": "http://g/v1",
      "personality": "p", "goals": ["g"], "adjudication_grace_turns": 4}, "must be 0..3"),
    ({"mode": "oracle"}, "mode must be one of"),
])
def test_rejects_invalid_master_config(tmp_path, master, match):
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump({"master": master, "civs": [
        {"player": 1, "provider": "local", "model": "m"}]}))
    with pytest.raises(ValueError, match=match):
        load_experiment(path)
```

- [ ] **Step 3: Run focused tests and confirm RED**

Run: `uv run pytest tests/arena/test_config.py tests/arena/test_experiment.py -q`

Expected: import/constructor failures for `MasterOptions` and `master`.

- [ ] **Step 4: Implement options, parser, and validation**

Add the exact `MasterOptions` record from Stable Interfaces. `active` is `mode != "off"`; `fingerprint()` returns all fields with goals as a list. Add `master: MasterOptions = field(default_factory=MasterOptions)` to `ArenaConfig` and include it in `arena_experiment_fingerprint`.

In `experiment.py`, add top-level `master`; an absent block yields `MasterOptions()` (`off`), while a present block with no `mode` defaults to `private_adviser`. Parse only `mode`, `provider`, `model`, `gateway`, `personality`, `goals`, `max_briefing_chars`, `memory_chars`, `timeout_s`, and `adjudication_grace_turns`. Goals must be a non-empty list of trimmed non-blank strings in active modes. Bounds: briefing/memory positive, timeout positive, grace 0–3. Active provider must be exactly `local`.

- [ ] **Step 5: Run GREEN and commit**

Run: `uv run pytest tests/arena/test_config.py tests/arena/test_experiment.py -q`

Expected: all focused tests pass.

```bash
git add src/civ_mcp/arena/config.py src/civ_mcp/arena/experiment.py tests/arena/test_config.py tests/arena/test_experiment.py
git commit -m "feat(arena): configure resident game master"
```

### Task 2: Master input, forced submission schema, prompt, and validation

**Files:**
- Create: `src/civ_mcp/arena/master.py`
- Create: `tests/arena/test_master.py`

**Interfaces:**
- Consumes: `MasterOptions`, typed channel projections, core narrative request DTO.
- Produces: all `master.py` Stable Interfaces; no persistence/network.

- [ ] **Step 1: Write failing validation and canary tests**

Create `tests/arena/test_master.py`:

```python
import json

from civ_mcp.arena.backends import Reply
from civ_mcp.arena.config import MasterOptions
from civ_mcp.arena.master import (
    MASTER_TOOL_SCHEMA, MasterInput, NarrativeRulingRequest,
    build_master_messages, validate_master_reply,
)


def options(mode="private_adviser"):
    return MasterOptions(mode=mode, provider="local", model="master",
        gateway="http://gpu1/v1", personality="Patient and theatrical",
        goals=("Create consequential choices",))


def projection_for_1(canary):
    return {"viewer": 1, "messages": [{"body": canary}]}


def input_with_request(deal_id, evidence_refs):
    request = NarrativeRulingRequest(
        deal_id=deal_id, proposer=1, counterparty=2, assigned_player=2,
        accepted_text="Keep the northern border peaceful.", deadline_turn=12,
        messages=(), evidence=tuple({"ref": ref} for ref in evidence_refs),
        allowed_evidence_refs=frozenset(evidence_refs),
    )
    return MasterInput.private(
        target_player=2, turn=14, game_briefing="PLAYER2-GAME",
        channel_projection={"viewer": 2}, memory="", ruling_requests=(request,),
    )


def ruling(deal_id, refs):
    return {"deal_id": deal_id, "verdict": "broken", "reason": "Observed breach",
            "evidence_refs": refs, "confidence": 0.8}


def structured_reply(arguments):
    return Reply(text=None, tool_calls=[{
        "id": "master-1", "name": "submit_master_briefing",
        "arguments": json.dumps(arguments),
    }])


def test_prompt_contains_only_typed_target_projection_in_private_mode():
    inp = MasterInput.private(
        target_player=1, turn=9, game_briefing="PLAYER1-GAME",
        channel_projection=projection_for_1("P1-CANARY"), memory="P1-MEMORY",
        ruling_requests=(),
    )
    text = json.dumps(build_master_messages(options(), inp))
    assert "P1-CANARY" in text and "P1-MEMORY" in text
    assert "P2-CANARY" not in text


def test_schema_exposes_one_submission_tool_and_no_actions():
    assert MASTER_TOOL_SCHEMA["function"]["name"] == "submit_master_briefing"
    props = MASTER_TOOL_SCHEMA["function"]["parameters"]["properties"]
    assert set(props) == {"briefing", "memory_update", "rulings"}


def test_invalid_ruling_does_not_discard_valid_briefing():
    inp = input_with_request("deal-000031", evidence_refs=("obs-1", "msg-1"))
    reply = structured_reply({
        "briefing": "Pressure them privately.", "memory_update": "Watch player 2.",
        "rulings": [{"deal_id": "deal-unknown", "verdict": "broken",
            "reason": "x", "evidence_refs": ["obs-1"], "confidence": 0.8}],
    })
    out = validate_master_reply(reply, inp, options())
    assert out.briefing == "Pressure them privately."
    assert out.rulings == ()
    assert out.ruling_errors == ("unknown narrative deal deal-unknown",)


def test_rejects_deterministic_deal_unavailable_evidence_and_duplicate_ruling():
    inp = input_with_request("deal-000031", evidence_refs=("obs-1",))
    out = validate_master_reply(structured_reply({
        "briefing": "b", "memory_update": "m", "rulings": [
            ruling("deal-000031", refs=["secret-ref"]),
            ruling("deal-000031", refs=["obs-1"]),
        ]}), inp, options())
    assert out.rulings == ()
    assert len(out.ruling_errors) == 2
```

- [ ] **Step 2: Run new tests and confirm RED**

Run: `uv run pytest tests/arena/test_master.py -q`

Expected: import failure because `master.py` does not exist.

- [ ] **Step 3: Implement typed records and forced schema**

Define frozen records:

```python
@dataclass(frozen=True)
class MasterRuling:
    deal_id: str
    verdict: Literal["honored", "broken", "unverifiable"]
    reason: str
    evidence_refs: tuple[str, ...]
    confidence: float

@dataclass(frozen=True)
class NarrativeRulingRequest:
    deal_id: str
    proposer: int
    counterparty: int
    assigned_player: int
    accepted_text: str
    deadline_turn: int
    messages: tuple[dict, ...]
    evidence: tuple[dict, ...]
    allowed_evidence_refs: frozenset[str]

@dataclass(frozen=True)
class MasterInput:
    mode: Literal["private_adviser", "director"]
    target_player: int
    turn: int
    game_briefing: str
    channel_projection: dict
    memory: str
    ruling_requests: tuple[NarrativeRulingRequest, ...]
    world_snapshot: MasterWorldSnapshot | None = None
```

The only tool schema is `submit_master_briefing`, requires all three fields, uses `additionalProperties: false` recursively, verdict enum, confidence 0–1, and arrays of strings for evidence.

- [ ] **Step 4: Implement prompt construction and independent validation**

`build_master_messages` emits a system message containing personality/goals, authority limits, mode, and explicit persuasion-only rule, then one JSON user payload built from `MasterInput`. Never concatenate untyped other-player state in private mode.

`validate_master_reply` requires exactly one `submit_master_briefing` call, JSON object args, briefing/memory strings clamped to configured bounds, and a list of rulings. Validate each ruling independently against supplied narrative request IDs/allowed refs; reject duplicates, non-finite/bool confidence, unknown verdict, blank/over-1,000 reason, and deterministic/unknown deals. Return valid briefing/memory even when every ruling is rejected.

- [ ] **Step 5: Run GREEN and commit**

Run: `uv run pytest tests/arena/test_master.py -q`

Expected: all pure master tests pass.

```bash
git add src/civ_mcp/arena/master.py tests/arena/test_master.py
git commit -m "feat(arena): validate structured game master output"
```

### Task 3: Resident master runtime, mode-scoped memory, idempotency, and transcripts

**Files:**
- Create: `src/civ_mcp/arena/master_runtime.py`
- Create: `tests/arena/test_master_runtime.py`

**Interfaces:**
- Consumes: Task 2 prompt/validation, `OpenAICompatBackend` protocol, `CostLog`, optional `ChannelRuntime`.
- Produces: `MasterRuntime.open/invoke/latest_briefing`, owner-only master memory/state/transcript files.

- [ ] **Step 1: Write failing memory, single-call, and fail-open tests**

Create `tests/arena/test_master_runtime.py`:

```python
@pytest.mark.asyncio
async def test_one_backend_call_persists_private_memory_and_cost(tmp_path):
    backend = FakeBackend([structured_reply({
        "briefing": "Act cautiously.", "memory_update": "Player 1 fears war.", "rulings": []})])
    cost = RecordingCost()
    rt = MasterRuntime.open(tmp_path, options(), backend, cost, channels=None)
    result = await rt.invoke(target_player=1, turn=8, briefing=Briefing(text="GAME"),
        channel_projection=empty_projection(1))
    assert result.block == "== GAME MASTER BRIEFING ==\nAct cautiously."
    assert backend.calls == 1
    memory = json.loads((tmp_path / "channels/master/private/player_1.json").read_text())
    assert memory["text"] == "Player 1 fears war."
    assert cost.records[0]["role"] == "game_master"


@pytest.mark.asyncio
async def test_same_player_turn_handoff_is_idempotent(tmp_path):
    backend = FakeBackend([valid_reply("once")])
    rt = MasterRuntime.open(tmp_path, options(), backend, RecordingCost(), None)
    first = await rt.invoke(target_player=0, turn=9, briefing=Briefing(text="G"),
        channel_projection=empty_projection(0), handoff=True)
    second = await rt.invoke(target_player=0, turn=9, briefing=Briefing(text="G"),
        channel_projection=empty_projection(0), handoff=True)
    assert first == second
    assert backend.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError("slow"), RuntimeError("down"), malformed_reply()])
async def test_master_failure_returns_empty_block_and_records_error(tmp_path, failure):
    rt = MasterRuntime.open(tmp_path, options(), FakeBackend([failure]), RecordingCost(), None)
    result = await rt.invoke(target_player=1, turn=8, briefing=Briefing(text="G"),
        channel_projection=empty_projection(1))
    assert result.block == ""
    assert result.status == "error"
    assert result.transcript["validation_errors"]
```

- [ ] **Step 2: Run new tests and confirm RED**

Run: `uv run pytest tests/arena/test_master_runtime.py -q`

Expected: import failure because `master_runtime.py` does not exist.

- [ ] **Step 3: Implement secure state and mode-separated memory**

Paths are exactly `channels/master/state.json`, `channels/master/transcript.jsonl`, `channels/master/private/player_<id>.json`, and `channels/master/director.json`, all mode `0o600` under owner-only directories. Each memory payload has schema/run/mode/player/model/config fingerprint/updated turn/text. Any mismatch in run, mode, model, personality, goals, or fingerprint raises before reuse.

`state.json` persists completed call keys and latest per-player briefing. Call keys are `master:{run_id}:{mode}:{target_player}:{turn}:{"handoff" if handoff else "mover"}`.

- [ ] **Step 4: Implement invocation, transcript, cost, and fail-open**

Load the matching memory, build `MasterInput`, call exactly once:

```python
reply = await self.backend.chat(
    build_master_messages(self.options, master_input),
    [MASTER_TOOL_SCHEMA],
    tool_choice={"type": "function", "function": {"name": "submit_master_briefing"}},
    timeout_s=self.options.timeout_s,
)
```

Validate, persist a valid memory update, route valid rulings when channels exist, atomically persist call completion/latest briefing, append/fsync one transcript, and record cost with `role="game_master"`. Transcript fields include mode, target, model/gateway, normalized config, prompt/context hashes, visible record IDs, latency, usage, raw parsed args, ruling errors, memory chars, and contamination. Catch `Exception`, append an error transcript, mark the call complete with empty briefing, and return; never catch `BaseException`.

- [ ] **Step 5: Run GREEN and commit**

Run: `uv run pytest tests/arena/test_master_runtime.py tests/arena/test_master.py -q`

Expected: runtime/pure master tests pass.

```bash
git add src/civ_mcp/arena/master_runtime.py tests/arena/test_master_runtime.py
git commit -m "feat(arena): persist resident game master runtime"
```

### Task 4: Narrative proposal activation, adjudication assignment, grace, and grievances

**Files:**
- Modify: `src/civ_mcp/arena/channels.py`
- Modify: `src/civ_mcp/arena/channel_protocol.py`
- Modify: `src/civ_mcp/arena/channel_runtime.py`
- Modify: `tests/arena/test_channel_protocol.py`
- Modify: `tests/arena/test_channel_runtime.py`
- Modify: `tests/arena/test_master_runtime.py`

**Interfaces:**
- Consumes: core deterministic lifecycle and validated `MasterRuling` records.
- Produces: narrative activation flag, fixed due assignment, ruling application, two-turn grace, tagged normal grievance.

- [ ] **Step 1: Add failing narrative lifecycle tests**

```python
def test_core_mode_still_rejects_narrative():
    with pytest.raises(ValueError, match="active game master"):
        parse_channel_action("propose_deal", narrative_action(), actor=1,
            enabled_players=frozenset({1, 2}), rules=ChannelRules(), narrative_allowed=False)


@pytest.mark.asyncio
async def test_due_narrative_is_assigned_once_to_proposer(tmp_path, fake_gs):
    rt, deal = await accepted_narrative_deal(tmp_path, fake_gs, proposer=1, counterparty=2, due_turn=20)
    await rt.poll_unseated(fake_gs, turn=20, local_player_id=2)
    assert rt.due_narrative_requests(2, 20) == ()
    requests = rt.due_narrative_requests(1, 20)
    assert [r.deal_id for r in requests] == [deal.id]
    assert rt.due_narrative_requests(1, 20)[0].assigned_player == 1


@pytest.mark.asyncio
async def test_broken_master_ruling_creates_normal_tagged_grievance(tmp_path, fake_gs):
    rt, deal = await due_narrative_deal(tmp_path, fake_gs)
    rt.apply_master_rulings(1, 20, (MasterRuling(
        deal.id, "broken", "screen absent", ("obs-000009",), 0.82),),
        metadata={"mode": "private_adviser", "model": "master", "prompt_hash": "abc"})
    grievance = rt.state.grievances[-1]
    assert grievance.adjudication_source == "game_master"
    assert grievance.wronged == deal.proposer
    assert grievance.offender == deal.counterparty


@pytest.mark.asyncio
async def test_missing_ruling_retries_through_grace_then_unverifiable(tmp_path, fake_gs):
    rt, deal = await due_narrative_deal(tmp_path, fake_gs, due_turn=20, grace=2)
    assert rt.due_narrative_requests(deal.proposer, 21)
    await rt.poll_unseated(fake_gs, turn=23, local_player_id=None)
    assert rt.deal(deal.id).state == "unverifiable"
    assert rt.state.grievances == ()
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `uv run pytest tests/arena/test_channel_protocol.py tests/arena/test_channel_runtime.py tests/arena/test_master_runtime.py -q`

Expected: narrative remains rejected and runtime has no adjudication methods.

- [ ] **Step 3: Extend canonical deal state without weakening deterministic terms**

Add optional `adjudication_assignee`, `adjudication_due_turn`, `adjudication_grace_by_turn`, and `adjudication_status` to `Deal`, plus frozen `NarrativeAdjudicationRequest`, with serialization defaults for existing schema-1 snapshots. `master.py` imports/re-exports that request type as `NarrativeRulingRequest`. `ChannelRuntime.set_narrative_adjudicator(active, grace_turns)` controls validation. `channel_protocol` accepts only `{"term_type":"narrative","params":{"text":...}}` when active and enforces 1–1,000 characters.

At narrative favor deadline, append one assignment event targeting immutable `deal.proposer`; keep payment timing deterministic. Registered term types continue through `verify_term` and never receive assignment fields.

- [ ] **Step 4: Implement requests, rulings, and grace**

`due_narrative_requests(player, turn)` returns only assigned unresolved requests in scope, with accepted text, bilateral messages, persisted privacy-safe observations, legal party IDs, deadline, and allowed evidence refs. `apply_master_rulings` rejects wrong assignee, unknown/not-due/non-narrative deal, duplicates, and refs outside the request. `honored` satisfies favor and advances payment; `broken` applies the fixed favor-breach parties and normal magnitude/decay with master metadata; `unverifiable` terminates without grievance. No valid ruling by `grace_by_turn` terminates `unverifiable` after the assigned player's final inclusive opportunity.

- [ ] **Step 5: Run GREEN and commit**

Run: `uv run pytest tests/arena/test_channel_protocol.py tests/arena/test_channel_runtime.py tests/arena/test_master_runtime.py -q`

Expected: narrative lifecycle passes and all deterministic/payment regressions remain green.

```bash
git add src/civ_mcp/arena/channels.py src/civ_mcp/arena/channel_protocol.py src/civ_mcp/arena/channel_runtime.py tests/arena/test_channel_protocol.py tests/arena/test_channel_runtime.py tests/arena/test_master_runtime.py
git commit -m "feat(arena): adjudicate narrative channel promises"
```

### Task 5: Private-adviser isolation, director world context, and contamination

**Files:**
- Modify: `src/civ_mcp/arena/master.py`
- Modify: `src/civ_mcp/arena/master_runtime.py`
- Modify: `src/civ_mcp/arena/channel_runtime.py`
- Modify: `tests/arena/test_master.py`
- Modify: `tests/arena/test_master_runtime.py`
- Modify: `tests/arena/test_channel_runtime.py`

**Interfaces:**
- Consumes: `ChannelRuntime.project_for_player/project_all`, existing `GameState.get_game_overview/get_diplomacy/get_victory_progress`.
- Produces: `build_master_world_snapshot(gs)`, strict private input routing, one director namespace, permanent contamination.

- [ ] **Step 1: Add failing cross-player canary and director tests**

```python
@pytest.mark.asyncio
async def test_private_canary_never_crosses_prompt_response_memory_block_or_transcript(tmp_path):
    backend = InspectingBackend([valid_reply("P1 advice", memory="P1-CANARY"), valid_reply("P2 advice")])
    channels = channels_with_private_canaries("P1-SECRET", "P2-SECRET")
    rt = MasterRuntime.open(tmp_path, private_options(), backend, RecordingCost(), channels)
    p1 = await rt.invoke(target_player=1, turn=8, briefing=Briefing(text="G1"),
        channel_projection=channels.project_for_player(1, 8))
    p2 = await rt.invoke(target_player=2, turn=8, briefing=Briefing(text="G2"),
        channel_projection=channels.project_for_player(2, 8))
    p2_artifacts = json.dumps([backend.prompts[1], p2.transcript, p2.block,
        (tmp_path / "channels/master/private/player_2.json").read_text()])
    assert "P1-SECRET" not in p2_artifacts
    assert "P1-CANARY" not in p2_artifacts


@pytest.mark.asyncio
async def test_channel_disabled_target_gets_explicit_empty_projection(tmp_path):
    rt = MasterRuntime.open(tmp_path, private_options(), InspectingBackend([valid_reply("b")]), RecordingCost(), None)
    await rt.invoke(target_player=4, turn=9, briefing=Briefing(text="G"),
        channel_projection=ChannelProjection(player_id=4))
    assert '"messages": []' in json.dumps(rt.backend.prompts[0])


@pytest.mark.asyncio
async def test_director_gets_full_projection_global_snapshot_and_marks_contamination(tmp_path):
    channels = channels_with_private_canaries("P1", "P2")
    rt = MasterRuntime.open(tmp_path, director_options(), InspectingBackend([valid_reply("direct")]), RecordingCost(), channels)
    result = await rt.invoke(target_player=1, turn=8, briefing=Briefing(text="G1"),
        channel_projection=channels.project_for_player(1, 8), gs=WorldGS())
    prompt = json.dumps(rt.backend.prompts[0])
    assert "P1" in prompt and "P2" in prompt and "world_snapshot" in prompt
    assert channels.state.privacy_contaminated is True
    assert result.transcript["privacy_contaminated"] is True
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `uv run pytest tests/arena/test_master.py tests/arena/test_master_runtime.py tests/arena/test_channel_runtime.py -q`

Expected: director lacks global context/contamination and isolation regressions fail.

- [ ] **Step 3: Implement bounded typed world snapshot**

`build_master_world_snapshot(gs)` concurrently calls only the three existing typed read methods, then returns a JSON-safe `MasterWorldSnapshot` containing turn, rankings/score, current yield summary, each known rival's military/city summary, and victory summaries. Clamp serialized world context to 12,000 characters by dropping lowest-priority verbose fields in the order `victory_details`, `rival_details`, then `rankings`; never include raw Lua, filesystem paths, tool handles, or model objects. Persist the exact bounded snapshot inside that director call's master transcript so later adjudication can be audited against what the model saw.

- [ ] **Step 4: Route mode-specific context and contaminate durably**

Private mode ignores `gs`, uses only the supplied target projection/matching private memory/target narrative requests, and rejects loading a memory path for another player. Director mode sets its own persisted master-state contamination flag at runtime open and, when channels exist, calls `channels.mark_privacy_contaminated("director_mode", source_id)`. It uses `project_all` (or an explicit empty full projection when channels are disabled), global snapshot, all due narrative requests, and `director.json`. The returned briefing remains target-specific. Contamination is monotonic; no event or mode switch clears it.

- [ ] **Step 5: Run GREEN and commit**

Run: `uv run pytest tests/arena/test_master.py tests/arena/test_master_runtime.py tests/arena/test_channel_runtime.py -q`

Expected: canary, empty-projection, director context, and contamination tests pass.

```bash
git add src/civ_mcp/arena/master.py src/civ_mcp/arena/master_runtime.py src/civ_mcp/arena/channel_runtime.py tests/arena/test_master.py tests/arena/test_master_runtime.py tests/arena/test_channel_runtime.py
git commit -m "feat(arena): scope game master privacy modes"
```

### Task 6: Forced backend calls, separate master cost role, construction, and preflight

**Files:**
- Modify: `src/civ_mcp/arena/backends.py:34-71`
- Modify: `src/civ_mcp/arena/cost.py:14-48`
- Modify: `src/civ_mcp/arena/arena.py:18-225`
- Modify: `tests/arena/test_backends.py`
- Modify: `tests/arena/test_cost.py`
- Modify: `tests/arena/test_arena_wiring.py`

**Interfaces:**
- Consumes: `MasterOptions`, `MasterRuntime.open`, current mover backend construction.
- Produces: backward-compatible `OpenAICompatBackend.chat(..., tool_choice=None, timeout_s=None)`, cost role, `build_master_runtime`.

- [ ] **Step 1: Add failing backend/cost/construction tests**

```python
@pytest.mark.asyncio
async def test_backend_forwards_forced_tool_and_master_timeout(fake_create):
    backend = backend_with_create(fake_create)
    await backend.chat([{"role": "user", "content": "x"}], [{"type": "function"}],
        tool_choice={"type": "function", "function": {"name": "submit_master_briefing"}},
        timeout_s=60.0)
    sent = fake_create.kwargs
    assert sent["tool_choice"]["function"]["name"] == "submit_master_briefing"
    assert sent["timeout"] == 60.0


def test_cost_log_keeps_player_shape_and_summarizes_master_role(tmp_path):
    log = CostLog(str(tmp_path / "cost.jsonl"))
    log.record(1, "mover", "local", 10, 5, 8)
    log.record(1, "master", "local", 20, 10, 8, role="game_master")
    records = [json.loads(line) for line in (tmp_path / "cost.jsonl").read_text().splitlines()]
    assert "role" not in records[0]
    assert records[1]["role"] == "game_master"
    assert log.summary()["by_role"]["game_master"]["prompt_tokens"] == 20


def test_builds_one_master_backend_separate_from_all_mover_backends(tmp_path):
    cfg = two_gpu_config()
    policies, mover_backends = build_policies(cfg.players, RecordingCost(), cfg)
    channels = ChannelRuntime.open(tmp_path, "r", frozenset({1, 2}), cfg.channel_rules)
    master, master_backend = build_master_runtime(cfg, tmp_path, RecordingCost(), channels=channels)
    assert len(mover_backends) == 2
    assert master_backend.base_url == "http://192.168.20.196:11441/v1"
    assert master_backend.model == "qwen3.6-27b"
    assert master.backend is master_backend
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `uv run pytest tests/arena/test_backends.py tests/arena/test_cost.py tests/arena/test_arena_wiring.py -q`

Expected: `chat` rejects new kwargs, cost rejects role, and master constructor is missing.

- [ ] **Step 3: Extend backend and cost without changing mover defaults**

Change signature to:

```python
async def chat(self, messages: list[dict], tools: list[dict], *,
               tool_choice: str | dict | None = None,
               timeout_s: float | None = None) -> Reply:
    kw = dict(
        model=self.model, messages=messages, max_tokens=MAX_COMPLETION_TOKENS,
        timeout=REQUEST_TIMEOUT_S if timeout_s is None else timeout_s,
    )
    if tools:
        kw["tools"] = tools
        kw["tool_choice"] = "auto" if tool_choice is None else tool_choice
```

Existing mover calls therefore remain byte-for-byte equivalent. Add keyword-only `role: str | None = None` to `CostLog.record`; only emit the JSON key when non-`None`. `summary()` adds `by_role` while retaining `by_player`/`total_usd`.

- [ ] **Step 4: Construct/preflight exactly one master backend**

`build_master_runtime(cfg, run_dir, cost, channels)` returns `(None, None)` when off. Otherwise construct one `OpenAICompatBackend(cfg.master.gateway, env_api_key, cfg.master.model)` and one `MasterRuntime.open`. In `_run`, pre-open the single `ChannelRuntime` when channels are enabled, then build the master against that object, preflight the master backend exactly once alongside each distinct mover backend, and pass both `channel_runtime=channels` and `master_runtime=master` into `run_arena`. With channels disabled, pass `channels=None`; the master still operates with an explicit empty projection. Dry-run mode constructs no live master backend and uses a deterministic fake master only in tests.

- [ ] **Step 5: Run GREEN and commit**

Run: `uv run pytest tests/arena/test_backends.py tests/arena/test_cost.py tests/arena/test_arena_wiring.py -q`

Expected: backend, cost, and construction tests pass.

```bash
git add src/civ_mcp/arena/backends.py src/civ_mcp/arena/cost.py src/civ_mcp/arena/arena.py tests/arena/test_backends.py tests/arena/test_cost.py tests/arena/test_arena_wiring.py
git commit -m "feat(arena): construct separate resident master backend"
```

### Task 7: Sequential coordinator invocation and mover-only briefing injection

**Files:**
- Modify: `src/civ_mcp/arena/coordinator.py:405-2060`
- Modify: `src/civ_mcp/arena/agent.py:88-281`
- Modify: `src/civ_mcp/arena/cli_agent.py:485-654`
- Modify: `src/civ_mcp/arena/prompting.py:50-91`
- Modify: `tests/arena/test_coordinator.py`
- Modify: `tests/arena/test_agent.py`
- Modify: `tests/arena/test_cli_agent.py`
- Modify: `tests/arena/test_prompting.py`

**Interfaces:**
- Consumes: `MasterRuntime.invoke`, core `ChannelAdmission`, existing `Briefing`/attention/policy kwargs.
- Produces: optional `master_runtime` parameter, one shared deterministic briefing, exact sequential ordering, `master_block` metadata.

- [ ] **Step 1: Add failing sequential/sleep/wake/injection tests**

```python
@pytest.mark.asyncio
async def test_master_completes_before_mover_and_only_briefing_is_injected():
    order = []
    master = RecordingMaster(order, result=MasterCallResult(
        block="== GAME MASTER BRIEFING ==\nPERSUADE", briefing="PERSUADE",
        call_key="k", status="ok", transcript={"memory_update": "PRIVATE", "rulings": ["PRIVATE-RULING"]}))
    mover = RecordingPolicy(order)
    await run_arena(one_turn_conn(), FakeGS(), master_config(), policy=mover, master_runtime=master)
    assert order == ["master", "mover"]
    prompt = mover.kwargs["master_block"]
    assert "PERSUADE" in prompt
    assert "PRIVATE" not in prompt and "PRIVATE-RULING" not in prompt


@pytest.mark.asyncio
async def test_sleep_skips_master_but_channel_wake_runs_both():
    sleeping_master, sleeping_mover = RecordingMaster([]), RecordingPolicy([])
    await run_arena(sleeping_conn(channel_wake=False), FakeGS(), master_config(attention="auto"),
        policy=sleeping_mover, master_runtime=sleeping_master)
    assert sleeping_master.calls == 0 and sleeping_mover.calls == 0
    wake_master, wake_mover = RecordingMaster([]), RecordingPolicy([])
    await run_arena(sleeping_conn(channel_wake=True), FakeGS(), master_config(attention="auto"),
        policy=wake_mover, master_runtime=wake_master)
    assert wake_master.calls == 1 and wake_mover.calls == 1


@pytest.mark.asyncio
async def test_master_failure_does_not_retry_or_suppress_mover():
    master = RecordingMaster([], result=empty_master_error())
    mover = RecordingPolicy([])
    await run_arena(one_turn_conn(), FakeGS(), master_config(), policy=mover, master_runtime=master)
    assert master.calls == 1 and mover.calls == 1
    assert mover.kwargs["master_block"] == ""
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `uv run pytest tests/arena/test_coordinator.py tests/arena/test_agent.py tests/arena/test_cli_agent.py tests/arena/test_prompting.py -q`

Expected: coordinator rejects `master_runtime` and never invokes it.

- [ ] **Step 3: Build/reuse deterministic briefing only after wake decision**

Add `master_runtime=None` to `run_arena`. After task/channel/attention pre-model work decides the seat will act, build one `Briefing` with existing `maybe_build_briefing` and pass the exact object to both master and mover. Do not build/call on a slept automated turn. If briefing build fails, pass `Briefing(errors=[...])`; the master still fails open and mover preserves existing behavior.

Call:

```python
master_result = await master_runtime.invoke(
    target_player=st.local, turn=st.turn, briefing=shared_briefing,
    channel_projection=channel_admission.projection if channel_admission else ChannelProjection(st.local),
    gs=gs if config.master.mode == "director" else None,
)
policy_kwargs["briefing"] = shared_briefing
policy_kwargs["master_block"] = master_result.block
```

This await must finish before `await pol(...)`. Catch only defensive integration `Exception`; `MasterRuntime` already records model failures.

- [ ] **Step 4: Preserve fixed block order and human-handoff idempotency seam**

API and CLI policy signatures already accept `master_block` from the core reserved slot; assert prompt order and add `"master": bool(master_block)` metadata. Add coordinator helper `invoke_master_for_handoff(master_runtime, player_id, turn, briefing, projection, gs)` that calls `invoke(..., handoff=True)`; the human-surface plan calls it once when the turn first becomes local. Runtime call keys make repeated polls no-ops.

- [ ] **Step 5: Run GREEN and commit**

Run: `uv run pytest tests/arena/test_coordinator.py tests/arena/test_agent.py tests/arena/test_cli_agent.py tests/arena/test_prompting.py -q`

Expected: sequential, sleep/wake, failure, and prompt tests pass.

```bash
git add src/civ_mcp/arena/coordinator.py src/civ_mcp/arena/agent.py src/civ_mcp/arena/cli_agent.py src/civ_mcp/arena/prompting.py tests/arena/test_coordinator.py tests/arena/test_agent.py tests/arena/test_cli_agent.py tests/arena/test_prompting.py
git commit -m "feat(arena): brief civ movers through resident master"
```

### Task 8: Master analysis and complete offline failure/privacy regression suite

**Files:**
- Modify: `src/civ_mcp/arena/analyze.py:803-1202`
- Modify: `tests/arena/test_analyze.py`
- Modify: `tests/arena/test_master_runtime.py`
- Modify: `tests/arena/test_coordinator.py`

**Interfaces:**
- Consumes: master transcript/cost records and contamination metadata.
- Produces: `analyze_master`, backward-compatible run report, exhaustive offline gate.

- [ ] **Step 1: Add failing analysis tests**

```python
def test_master_analysis_separates_cost_failures_rulings_and_contamination():
    report = analyze_master(master_transcript_fixture(), master_cost_fixture())
    assert report["calls"] == 4
    assert report["status"] == {"ok": 2, "error": 2}
    assert report["rulings"] == {"honored": 1, "broken": 1, "unverifiable": 0}
    assert report["cost"]["prompt_tokens"] == 120
    assert report["privacy_contaminated"] is True


def test_old_run_without_master_remains_analyzable():
    report = analyze(player_transcripts_only(), player_costs_only())
    assert report["master"] == {"enabled": False}
```

- [ ] **Step 2: Run analysis and all master tests to confirm RED**

Run: `uv run pytest tests/arena/test_analyze.py tests/arena/test_master.py tests/arena/test_master_runtime.py tests/arena/test_coordinator.py -q`

Expected: no master analysis section.

- [ ] **Step 3: Implement master analysis**

Read `channels/master/transcript.jsonl` when present. Aggregate mode/model/target, status/error kind, latency/usage, valid/invalid ruling counts/verdicts, memory characters, and contamination. Pull only `role=game_master` costs into master totals. Older runs and `master.mode=off` produce `{"enabled": false}` without changing player metrics.

- [ ] **Step 4: Complete offline matrix**

Add parameterized tests for timeout, backend exception, zero/multiple/wrong tool calls, malformed JSON, over-limit strings, unknown/duplicate/deterministic rulings, unavailable refs, private memory mismatch, director/private namespace mismatch, incompatible config resume, channel-disabled mover, automated sleep, channel wake, and repeated handoff. Each asserts mover continuation or pre-admission failure exactly as the spec requires.

- [ ] **Step 5: Run GREEN and commit**

Run: `uv run pytest tests/arena/test_analyze.py tests/arena/test_master.py tests/arena/test_master_runtime.py tests/arena/test_coordinator.py -q`

Expected: full offline master matrix passes.

```bash
git add src/civ_mcp/arena/analyze.py tests/arena/test_analyze.py tests/arena/test_master_runtime.py tests/arena/test_coordinator.py
git commit -m "test(arena): cover game master privacy and failures"
```

### Task 9: Two-GPU experiment configs, attended gate, and full verification

**Files:**
- Create: `experiments/arena-master-private-smoke.yaml`
- Create: `experiments/arena-master-director-smoke.yaml`
- Create: `docs/superpowers/plans/2026-07-16-arena-game-master-live-gate.md`

**Interfaces:**
- Consumes: all prior tasks and existing per-GPU llama.cpp endpoints.
- Produces: repeatable residency/privacy/narrative/outage evidence.

- [ ] **Step 1: Create exact private and director configs**

Private config:

```yaml
run_id: arena-master-private-smoke
max_game_turns: 20
master:
  mode: private_adviser
  provider: local
  model: qwen3.6-27b
  gateway: http://192.168.20.196:11441/v1
  personality: "Patient, theatrical, and fascinated by shifting alliances."
  goals:
    - "Create consequential diplomatic choices."
    - "Prevent one civilization from becoming unchallenged too early."
  max_briefing_chars: 1200
  memory_chars: 4000
  timeout_s: 60
  adjudication_grace_turns: 2
civs:
  - player: 1
    provider: local
    model: gemma4-26b
    gateway: http://192.168.20.196:11440/v1
    tools: full
    channels: {enabled: true}
  - player: 2
    provider: local
    model: gemma4-26b
    gateway: http://192.168.20.196:11440/v1
    tools: full
    channels: {enabled: true}
```

Director config is identical except `run_id: arena-master-director-smoke` and `mode: director`.

- [ ] **Step 2: Write the exact attended checklist**

Require `offload-discipline`, `gpu-status`, `litellm-gateway` only for read-only endpoint verification, `civ6-arena-live` for the run, and `verification-before-completion`. Record `nvidia-smi` before/during/after, `/v1/models` for both endpoints, process PIDs/VRAM, run IDs, save, commits, master/player transcripts, channel state, and analysis. Never issue an extra exploratory model completion outside the arena run.

- [ ] **Step 3: Run private adviser residency/privacy/narrative gate**

Run:

```bash
uv run civ-arena --config experiments/arena-master-private-smoke.yaml
uv run civ-arena-analyze arena_runs/arena-master-private-smoke
```

Verify qwen stays resident/routed on physical GPU 1 (`:11441`) and gemma on physical GPU 0 (`:11440`); calls never overlap; P1 canary is absent from P2 prompt/raw response/memory/mover block/transcript; a narrative promise yields a validated tagged grievance; stopping the master endpoint produces an error record while the mover completes with deterministic context.

- [ ] **Step 4: Run director contamination/persuasion gate**

Run:

```bash
uv run civ-arena --config experiments/arena-master-director-smoke.yaml
uv run civ-arena-analyze arena_runs/arena-master-director-smoke
```

Verify the prompt contains bounded global snapshot/full channel projection, briefing remains target-specific and persuasive, and channel state/master transcript/analysis all permanently mark contamination.

- [ ] **Step 5: Run final verification and commit**

Run:

```bash
uv run pytest tests/ -q
git diff --check
git status --short --branch
```

Expected: full suite passes; diff check is silent; live checklist has no unresolved item; only intentional master files and pre-existing `.serena/memories/` appear.

```bash
git add experiments/arena-master-private-smoke.yaml experiments/arena-master-director-smoke.yaml docs/superpowers/plans/2026-07-16-arena-game-master-live-gate.md
git commit -m "test(arena): gate resident game master on two GPUs"
```

Leave `arena-game-master` unmerged for riz's separate-session review. The human-surface branch should start from this reviewed tip so seat-0 projections can include the already-tested latest master briefing and contamination metadata.
