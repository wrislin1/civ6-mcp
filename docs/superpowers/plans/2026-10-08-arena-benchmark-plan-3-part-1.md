# Arena benchmark Plan 3 Part 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the authoring toolkit, signed scoring contract, and three script-validated development positions before any library model trials.

**Architecture:** Keep `SingleTurnAgent`, `BenchmarkRunner`, registry dispatch, and the atomic evidence store as the execution path. Add a scripted backend at `RunnerDependencies.make_agent`, separate version-2 evidence/scoring/report modules, and a recipe-driven authoring CLI that reuses production deployment and reload verification. Complete software validation and the historical classifier regression offline before starting any live authoring clock.

**Tech Stack:** Existing Python 3.12+, uv, asyncio, pytest/pytest-asyncio, PyYAML, FireTuner Lua, and the Windows launcher bridge; no new service or Python dependency.

**Spec:** [Approved Part 1 design](../specs/2026-10-08-arena-benchmark-plan-3-part-1-design.md), including the written-review amendments dated 2026-10-08. The [parent design](../specs/2026-08-30-arena-controlled-position-benchmark-design.md) governs later screen and advancement rules.

## Global Constraints

- Evidence, predicate, and report schema versions are `2.0.0`. Preserve version-1 interpretation and the frozen Plan 2 admission restrictions; reproduce exact old reports with their pinned revision and fingerprint.
- **No Plan 3 primary score or primary-score improvement is comparable to a Plan 2 primary score or improvement.** Observation earns zero; partial credit is an observable endpoint, never movement toward a target.
- `primary_score = (gross_credit - harm_total) / M`, without clipping or shifting. Each position has three four-point objectives, `M = 12`, optional two-point intermediates, and a finite penalty maximum no greater than 12.
- Default harm weight equals the associated objective's maximum, four points here. Declare exceptions and compensation before model exposure. Deduplicate losses; no survival-credit objective or separate missed-objective debit.
- `max_steps = 15` counts backend round trips, including a finish-only response. Report non-finish tool-call attempts per round trip and dispatched calls. Never infer rounds from tool rows.
- Each version-2 capture has a **2.0-second hard wall limit**, including query, parse, normalisation, and digest. Capture failures and capture cancellation by the episode deadline are infrastructure failures.
- Scripted validation uses `actor_kind: scripted`, `counting: false`, `max_steps: 15`, and `episode_wall_s: 300`. Model endpoint, model seed, token usage, cost, and model latency are not applicable. The scripted wall is an operational limit, not model admission evidence.
- Later model blocks use `max(300, ceil(15 * p95_roundtrip_s * 1.5))`. Common context, completion-token, and result-character limits are frozen before model exposure.
- Freeze the exact 35-tool list below as `plan3-part1-v1`; append `finish_trial` only as agent control. No `end_turn`, mutable tier alias, briefing/playbook implementation, tracker arm, or decision-model arm in Part 1.
- No tested-model episode on a library position in Part 1. All nine rubrics freeze together before viewing tested-model transcripts. Any future development pilot requires an explicit amendment and persistent `pilot-informed` provenance; held-out positions have no exception.
- Three elapsed live hours per scenario attempt, including survey, reload waits, retries, interruptions, verification, and scripted cases. One declared material scenario substitution per family permits a fresh clock; retain both attempts. A second failure blocks that family.
- Reuse existing before/after capture points and twelve-cycle verification. No extra capture loop, historical-mode classifier, hidden script repair, fabricated historical measurements, or new primary utility score.
- Complete the 117-mutation regression offline before live authoring. Tile-yield and loss ledgers report from day one and never affect advancement.
- Live work follows the [arena operating playbook](../../../tools/skills/civ6-arena-live/SKILL.md): one FireTuner owner, matching Windows companion code, verified reloads, and restored final state.

---

## File and responsibility map

Paths below are relative to the repository root. Existing modules retain their existing responsibilities; add narrow version dispatch or injected dependencies rather than copying their execution loops.

| Files | Responsibility |
|---|---|
| `benchmarks/toolsets/plan3-part1-v1.yaml`; `arena/benchmark_contract_v2.py`, `arena/benchmark_manifest_v2.py` under `src/civ_mcp/` | Explicit tools and strict version-2 position/script/case/lock validation; dependency fingerprints. |
| `src/civ_mcp/lua/benchmark_v2.py`; `src/civ_mcp/arena/benchmark_state_v2.py` | Complete wire query/parser, canonical evidence, coverage and entity identity. |
| `src/civ_mcp/arena/benchmark_capture.py` | Bounded capture with timing outside the digest and cancellation attribution. |
| `src/civ_mcp/arena/benchmark_lifecycle.py`, `benchmark_predicates_v2.py`, `benchmark_scoring_v2.py` | Evidence-based lifecycle classification, shared cover geometry, endpoint predicates, event losses, compensation, signed score. |
| `src/civ_mcp/arena/benchmark_ledger.py`, `benchmark_audit.py` | Raw measured deltas, lifecycle/loss coverage, one general mutation classifier. |
| `src/civ_mcp/arena/benchmark_scripted.py`; existing `benchmark_agent.py`, `benchmark_runner.py`, `benchmark_schedule.py` | Finite response batches, counters, scripted trial identity, injected capture, existing execution/persistence. |
| `src/civ_mcp/arena/benchmark_report_v2.py`, `benchmark_validation.py` | Derived reports, locked validation schedule, expected-versus-actual comparison, separate model aggregation admission. |
| `src/civ_mcp/arena/benchmark_authoring_journal.py`, `benchmark_authoring.py` | Persistent scenario clocks, replayable authoring stages, final packet assembly. |
| Existing `benchmark_position.py`, `benchmark_deploy.py`, `game_launcher.py`, `launcher_cli.py` | Inject version-2 capture into existing verification; export immutable archives through the Windows bridge. |
| `tests/arena/benchmark_v2_fixtures.py`, `tests/arena/fixtures/` and task-specific tests below | Small explicit state factories, wire fixtures, historical expected membership, runner integration. |
| `benchmarks/recipes/`, `scripts/`, `validation/`, `positions/`, `provenance/`, `saves/`, `contracts/` | Versioned recipes and final position packets; concrete live bindings are authoring outputs. |
| `docs/research/arena-benchmark-plan-3-part-1-exit.md` | Gate evidence, authoring costs including failures, budget justification, and remaining roadmap deltas. |

Version-2 modules use `dict[str, Any]` for JSON records, with strict validators at their boundaries. They do not expose unvalidated arbitrary dictionaries to evaluation. Unit references are `(owner, id)`, with the engine's full stable ID in evidence and a separately recorded arena `unit_index` for dispatch. Do not confuse `id % 65536` with a globally unique entity key.

Keep immutable authoring inputs separate from the final evidence packet. Freeze `benchmarks/provenance/<position-id>-authoring.json` before verification and scripted validation; the position and run lock hash that file. The later `benchmarks/provenance/<position-id>.json` packet references it, the position, validation runs, and the completed clock journal. Neither the position nor the run lock hashes this later packet, avoiding a circular digest or a provenance file that changes after admission. Failed and in-progress stages remain in the separate append-only authoring journal.

## Task 1: Freeze the tool surface and version-2 input contracts

**Files:**
- Create: `benchmarks/toolsets/plan3-part1-v1.yaml`, `src/civ_mcp/arena/benchmark_contract_v2.py`, `src/civ_mcp/arena/benchmark_manifest_v2.py`.
- Test: `tests/arena/test_benchmark_contract_v2.py`, `tests/arena/test_benchmark_manifest_v2.py`.
- Read: existing `benchmark_contract.py`, `benchmark_manifest.py`, `registry.py`, `benchmark_agent.py`.

**Interfaces:**
- Produces `load_toolset(path: Path) -> dict[str, Any]`, `load_v2_document(path: Path, *, kind: str) -> dict[str, Any]`, `validate_v2_document(raw: dict[str, Any], *, kind: str) -> None` in `benchmark_manifest_v2`.
- Produces `canonical_bytes(value: Any) -> bytes`, `document_digest(value: Any) -> str`, `implementation_fingerprint(root: Path) -> str` in `benchmark_contract_v2`.
- `load_toolset` returns `toolset_id`, ordered `game_tools`, resolved `schemas` including agent control, and `identity` covering the source document and resolved schemas. Execution source is additionally covered by the contract fingerprint.

- [ ] **Write rejection and registry-resolution tests.** Pin all 35 names in the test, including their order; test aliases, duplicates, unknown names, `end_turn`, and a changed schema. Keep existing version-1 loader rejection tests.

```python
from pathlib import Path
import pytest
from civ_mcp.arena.benchmark_manifest_v2 import load_toolset

def test_part1_toolset_is_explicit_and_has_production_discovery():
    tools = load_toolset(Path("benchmarks/toolsets/plan3-part1-v1.yaml"))
    assert tools["toolset_id"] == "plan3-part1-v1"
    assert len(tools["game_tools"]) == len(set(tools["game_tools"])) == 35
    assert tools["game_tools"][-6:] == [
        "get_city_production", "get_district_advisor", "get_purchasable_tiles",
        "purchase_tile", "get_pathing_estimate", "get_empire_resources",
    ]
    assert tools["schemas"][-1]["function"]["name"] == "finish_trial"
    assert "end_turn" not in tools["game_tools"]

def test_mutable_alias_is_rejected(tmp_path):
    path = tmp_path / "tools.yaml"
    path.write_text("toolset_id: bad\ngame_tools: standard\n")
    with pytest.raises(ValueError, match="explicit.*list"):
        load_toolset(path)
```

- [ ] **Run the new tests and confirm failure before implementation.** Run `uv run pytest tests/arena/test_benchmark_contract_v2.py tests/arena/test_benchmark_manifest_v2.py -q`; expect missing-module failures initially.
- [ ] **Create this exact toolset file and implement canonical hashing and strict loaders.** Reuse `resolved_benchmark_tools(explicit_names)`; do not alter `registry.TIERS["standard"]` or the Plan 2 loader.

```yaml
toolset_id: plan3-part1-v1
game_tools:
  - get_overview
  - get_units
  - get_cities
  - move_unit
  - found_city
  - set_city_production
  - set_research
  - fortify_unit
  - skip_unit
  - get_unit_promotions
  - promote_unit
  - get_map_area
  - get_tech_civics
  - attack_unit
  - get_builder_tasks
  - improve_tile
  - remove_feature
  - repair_improvement
  - get_great_people
  - recruit_great_person
  - activate_great_person
  - purchase_item
  - heal_unit
  - alert_unit
  - set_civic
  - get_pending_diplomacy
  - respond_to_diplomacy
  - get_pending_trades
  - respond_to_trade
  - get_city_production
  - get_district_advisor
  - get_purchasable_tiles
  - purchase_tile
  - get_pathing_estimate
  - get_empire_resources
```

```python
import hashlib
import json
from typing import Any

def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")

def document_digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()
```

Strict document envelopes, with unknown keys rejected and relative file paths resolved against the document's directory:

| Kind | Required content |
|---|---|
| `position` | `schema_version`, `position_id`, `version`, `family`, `split`, archive path/hash and game save name, player ID, expected state/hash, coverage, toolset path/identity, contract identity, rubric, provenance path/hash, `pilot_informed`. Only development positions are admitted by this Part 1 CLI. |
| `script` | `schema_version`, `script_id`, `batches`; each batch has `calls`, each call has `name` and object `arguments`. Last batch contains `finish_trial`; no later batch exists. No expectations or rubric keys. |
| `case` | `schema_version`, `case_id`, position/script references and digests, `tags`, `expected` containing objective credits, admitted/compensated harm IDs, gross/harm/net/primary, endpoint assertions, ledger assertions. |
| `validation_suite` | Schema and suite IDs, ordered case references/digests, position/toolset/contract identities, `max_steps: 15`, `episode_wall_s: 300`, explicit result-character cap, `actor_kind: scripted`, `counting: false`. |
| `lock` | Resolved immutable suite, script/case bytes or digests, archive/state/provenance identities, ordered schedule, code/schema identities, operational limits, actor identity; model/seed/token/cost/latency fields are null. |

Implement only these version-2 inputs; do not widen the existing model campaign loader. Fingerprint an explicit, sorted dependency list covering the new modules and the reused predicate/action attribution, agent, runner, store, registry/narration, `GameState`/Lua, reload/deployment, and position helpers. Reject a missing dependency file. Expand that list in the task introducing each new module; test that editing a scorer, classifier, query, tool schema, or dispatch dependency changes the fingerprint. Never claim that the new fingerprint equals the released v1 fingerprint.

- [ ] **Run the new tests plus `tests/arena/test_benchmark_contract.py` and `tests/arena/test_benchmark_manifest.py`.** Expect all pass, including existing rejection of unsupported Plan 2 arms/options.
- [ ] **Commit:** `git add benchmarks/toolsets/plan3-part1-v1.yaml src/civ_mcp/arena/benchmark_contract_v2.py src/civ_mcp/arena/benchmark_manifest_v2.py tests/arena/test_benchmark_contract_v2.py tests/arena/test_benchmark_manifest_v2.py`; `git commit -m "feat(benchmark): freeze Part 1 toolset and v2 input contracts"`.

## Task 2: Capture complete version-2 evidence at the existing snapshot points

**Files:**
- Create: `src/civ_mcp/lua/benchmark_v2.py`, `src/civ_mcp/arena/benchmark_state_v2.py`, `tests/arena/benchmark_v2_fixtures.py`, `tests/arena/fixtures/benchmark_state_v2.txt`.
- Test: `tests/arena/test_benchmark_state_v2.py`.
- Read: existing `lua/benchmark.py`, `lua/cities.py`, `lua/units.py`, `benchmark_state.py`.

**Interfaces:**
- Produces `build_benchmark_state_query_v2(player_id: int, coverage: dict[str, Any]) -> str` in `lua.benchmark_v2`.
- Produces `parse_state_v2(raw: str, *, coverage: dict[str, Any]) -> dict[str, Any]`, `normalize_state_v2(state: dict[str, Any]) -> dict[str, Any]`, `digest_state_v2(state: dict[str, Any]) -> str`, and `capture_state_v2(conn: Any, player_id: int, coverage: dict[str, Any]) -> Awaitable[dict[str, Any]]` in `benchmark_state_v2`.
- Produces test helper `state_v2(*, units=(), cities=(), tiles=(), targets=(), gold=100, faith=0) -> dict[str, Any]`; it emits all mandatory fields, turn 100, active/player ID 0, coverage and completeness metadata. Fixture entity rows must be fully explicit, not silently default missing health or hostility.

- [ ] **Write parser completeness and identity tests before code.** Store a complete representative wire response and test its truncated form, duplicate entity/plot rows, wrong row counts, absent health/visibility, NaN, unknown row type, and missing final sentinel. Test canonical sorting by owner/ID, nested building/queue rows, and tile coordinates.

```python
from pathlib import Path
import pytest
from civ_mcp.arena.benchmark_state import BenchmarkStateError
from civ_mcp.arena.benchmark_state_v2 import parse_state_v2, digest_state_v2

def test_truncated_v2_capture_cannot_make_units_disappear():
    raw = Path("tests/arena/fixtures/benchmark_state_v2.txt").read_text()
    coverage = {"include_owned_tiles": True, "area": [[10, 10], [11, 10]],
                "tracked_targets": [[1, 70001]]}
    with pytest.raises(BenchmarkStateError, match="incomplete"):
        parse_state_v2(raw.rsplit("END|", 1)[0], coverage=coverage)

def test_capture_order_does_not_change_digest():
    from .benchmark_v2_fixtures import state_v2
    a = state_v2()
    b = dict(reversed(list(a.items())))
    assert digest_state_v2(a) == digest_state_v2(b)
```

- [ ] **Run `uv run pytest tests/arena/test_benchmark_state_v2.py -q`.** Expect import failures, then explicit missing-completeness failures while developing the parser.
- [ ] **Implement a versioned, complete wire protocol and canonical representation.** The query emits `BEGIN|2.0.0`, counted row families, and `END|<counts>` only after all required reads succeed. Catch Lua read errors as capture errors, not omitted rows. Include all owned units/cities, all owned tiles plus the frozen discoverable area, current resource access, and explicit status for each tracked tactical target. A target status distinguishes `alive_visible`, `alive_not_visible`, and verified `destroyed`; an absent row is an error, not a kill.

Canonical fields:

| Record | Required evidence |
|---|---|
| Root | Schema, turn, active/player ID, civ/seed identity already captured in v1, gold/faith, measured resource access, coverage identity and completeness. |
| Owned unit | Owner, stable ID, arena index, type, civilian/combat role, x/y, current/max HP, moves, charges. |
| Tracked/visible hostile | Owner/ID, role, hostility, current visibility, x/y and HP where visible, independently verified lifecycle status for scored targets. Hidden private facts must never enter model observations. |
| City | Owner/ID, x/y, population/housing, building presence/pillage, districts/placement/construction, active queue item/type/repair flag and target tile. |
| Tile | x/y, owner, terrain/feature, resource, improvement/pillage, district, all six yields, current visibility. |

Reuse verified getter patterns: `GetMovesRemaining()`, `GetMaxDamage() - GetDamage()`, `GetMaxDamage()`, `GetGrowth():GetHousing()`, plot `GetYield(0..5)`, and existing production/repair readback. Account explicitly for the InGame `GetCurrentProductionTypeHash()` versus GameCore `CurrentlyBuilding()` distinction used in `build_verify_production`; do not infer a repair from a narrative string. If an essential value is unavailable, reject authoring that objective until it can be captured.

```python
def require_complete(actual: dict[str, int], declared: dict[str, int]) -> None:
    from civ_mcp.arena.benchmark_state import BenchmarkStateError
    if actual != declared:
        raise BenchmarkStateError("incomplete v2 capture: row counts disagree")
```

Put `require_complete` in `benchmark_state_v2`. `GameConnection.execute_read` returns lines, so `capture_state_v2` passes `"\n".join(lines)` to `parse_state_v2`. Validate counts and required fields before normalisation; sort every semantic set, canonicalise integral floats using the existing v1 numeric convention, and reject nonfinite numbers. `digest_state_v2` uses Task 1's canonical bytes on the validated, normalised state. Timing is not a state field. Resource access and yields carry observed/unavailable coverage explicitly; a field required by scoring cannot be unavailable.

- [ ] **Run new state tests and `tests/arena/test_benchmark_state.py`.** Expect deterministic digests and unchanged v1 wire parsing. Pin v2 normalisation as idempotent under the existing `state_digest` wrapper so injected v2 states cannot get a different digest at old call sites.
- [ ] **Commit the Task 2 files** with `git commit -m "feat(benchmark): capture complete v2 scoring evidence"` after staging only those paths and the fingerprint-list update.

## Task 3: Bound capture cost and preserve infrastructure attribution

**Files:**
- Create: `src/civ_mcp/arena/benchmark_capture.py`.
- Modify: `src/civ_mcp/arena/benchmark_agent.py`, `src/civ_mcp/arena/benchmark_runner.py`, `src/civ_mcp/arena/benchmark_position.py`.
- Test: `tests/arena/test_benchmark_capture.py`, existing `test_benchmark_agent.py`, `test_benchmark_runner.py`, `test_benchmark_position.py`.

**Interfaces:**
- Produces `CaptureFailure(BenchmarkStateError)` and `CaptureTelemetry` with `records: list[dict[str, Any]]`, `interrupted: bool`, `reset() -> None`, `summary(*, episode_wall_s: float) -> dict[str, Any]`.
- Produces `capture_bounded(read: Callable[[], Awaitable[dict[str, Any]]], *, phase: str, telemetry: CaptureTelemetry, limit_s: float = 2.0) -> Awaitable[tuple[dict[str, Any], str]]`. Production v2 admission permits only 2.0; smaller values are test injection.
- Adds optional `capture_telemetry` to `SingleTurnAgent` and `RunnerDependencies`. Adds optional keyword `capture_state` injection to `capture_position`/`verify_position`, defaulting to their existing v1 function/signature `(conn, player_id, relevant_tiles)`; the v2 adapter closes over coverage. Existing callers keep their behavior.

- [ ] **Write bounded-read and outer-deadline cancellation tests.** Use event-driven cancellation rather than multi-second sleeps. Also test a synchronous parse/digest overrun, timing exclusion from digest, and a successful read after telemetry reset.

```python
import asyncio
import pytest
from civ_mcp.arena.benchmark_capture import CaptureFailure, CaptureTelemetry, capture_bounded

@pytest.mark.asyncio
async def test_episode_deadline_during_capture_is_infrastructure():
    telemetry = CaptureTelemetry()
    async def blocked_read():
        await asyncio.Event().wait()
    with pytest.raises(CaptureFailure):
        async with asyncio.timeout(0.01):
            await capture_bounded(blocked_read, phase="tool_before", telemetry=telemetry)
    assert telemetry.interrupted
    assert telemetry.records[-1]["complete"] is False
```

- [ ] **Run `uv run pytest tests/arena/test_benchmark_capture.py -q` and confirm the new invariant fails before the wrapper exists.**
- [ ] **Implement timing around the complete capture and digest, at existing capture sites only.** Use `time.monotonic()` and `asyncio.timeout(2.0)`. Check elapsed time after CPU work as well: an asyncio deadline alone cannot interrupt synchronous parsing. Never admit an over-budget result even if the event loop did not get a cancellation opportunity. Record failed duration/status in `finally`, outside canonical state.

```python
import asyncio
import time
from civ_mcp.arena.benchmark_state_v2 import digest_state_v2

async def capture_bounded(read, *, phase, telemetry, limit_s=2.0):
    started = time.monotonic()
    complete = False
    try:
        async with asyncio.timeout(limit_s):
            state = await read()
            digest = digest_state_v2(state)
        if time.monotonic() - started > limit_s:
            raise CaptureFailure("capture exceeded wall limit")
        complete = True
        return state, digest
    except asyncio.CancelledError as exc:
        telemetry.interrupted = True
        raise CaptureFailure("capture interrupted by enclosing deadline") from exc
    except TimeoutError as exc:
        telemetry.interrupted = True
        raise CaptureFailure("capture exceeded wall limit") from exc
    finally:
        telemetry.records.append({"phase": phase, "duration_s": time.monotonic() - started,
                                  "complete": complete})
```

The production `CaptureFailure` inherits the existing `BenchmarkStateError`, so the runner's harness-crash path handles it before model timeout classification. Preserve the original exception in `SingleTurnAgent.run` when the outer timeout has also expired; do not replace it with `EpisodeTimedOut`. A captured cancellation requires the normal failed-attempt reload/reconnect before reuse. No `uncancel()` or catch-and-continue path.

Reset telemetry at the start of each runner attempt; record `initial`, `tool_before`, `tool_after`, and `final` phases, including failed attempts. The **total** includes root captures; the **episode share** includes only `tool_before`/`tool_after`, since `EpisodeEvidence.wall_clock_s` excludes initial/final runner captures. Use nearest-rank p95 and report count, mean, p95, max, total, and in-episode total; zero observations yields unavailable statistics, not a passing timing gate. Reuse the bounded capture adapter for capture/verify without adding redundant reads.

- [ ] **Run the new tests and the agent/runner/position tests.** Verify that ordinary backend timeouts retain their existing healthy/unhealthy classification, unverified reloads still abort, and exactly twelve successful cycles are still required. The runner test must inspect an infrastructure attempt file and absence of a committed scoreable trial for capture timeout.
- [ ] **Commit the Task 3 files and fingerprint update:** `git commit -m "feat(benchmark): bound snapshot cost and classify capture failures"`.

## Task 4: Implement endpoint predicates and one civilian-safety geometry

**Files:**
- Create: `src/civ_mcp/arena/benchmark_lifecycle.py`, `src/civ_mcp/arena/benchmark_predicates_v2.py`.
- Test: `tests/arena/test_benchmark_lifecycle.py`, `tests/arena/test_benchmark_predicates_v2.py`.
- Read: `src/civ_mcp/arena/action_metrics.py` for the existing offset-grid `_hex_distance`.

**Interfaces:**
- Produces `classify_lifecycle(step: dict[str, Any]) -> list[dict[str, Any]]` in `benchmark_lifecycle`, using recorded tool arguments/results and complete before/after snapshots. Each record names the entity, status (`lost`, `consumed`, `transformed`, or `unresolved`), and supporting facts.
- Produces `validate_predicate(predicate: dict[str, Any]) -> None`, `evaluate_predicate(predicate: dict[str, Any], *, initial: dict[str, Any], final: dict[str, Any], transition: dict[str, Any] | None = None) -> bool` in `benchmark_predicates_v2`. Event predicates receive the raw step as `transition`; endpoint predicates do not need it.
- Produces `civilian_covered(state: dict[str, Any], ref: tuple[int, int]) -> bool`, `civilian_exposed(state: dict[str, Any], ref: tuple[int, int]) -> bool`.
- All missing required facts raise `BenchmarkStateError`. Predicate kinds are finite; there is no arbitrary Python/Lua expression evaluator.

- [ ] **Write safety boundary and endpoint tests.** Cover both offset-row parities, co-located escorts, friendly versus hostile units, cities, invisible hostiles, lost/consumed civilians, and initial exposure. Pin completion independent of which builder acted and last-charge consumption.

```python
from copy import deepcopy
from civ_mcp.arena.benchmark_predicates_v2 import evaluate_predicate
from .benchmark_v2_fixtures import state_v2

def test_observation_cannot_create_exposure_debit():
    unit = dict(owner=0, id=1, unit_index=1, type="UNIT_BUILDER", role="civilian",
                x=10, y=10, hp=100, max_hp=100, moves=2, charges=1)
    foe = dict(owner=1, id=70001, role="combat", hostile=True, visible=True,
               x=11, y=10, hp=100, max_hp=100, status="alive_visible")
    initial = state_v2(units=[unit], targets=[foe])
    assert not evaluate_predicate(
        {"kind": "new_civilian_exposure", "unit": [0, 1]},
        initial=initial, final=deepcopy(initial),
    )
```

- [ ] **Run `uv run pytest tests/arena/test_benchmark_lifecycle.py tests/arena/test_benchmark_predicates_v2.py -q`; confirm failure.**
- [ ] **Implement and validate the following vocabulary.** Validate every branch before evaluating `all`/`any` so an invalid hidden branch cannot be skipped by short-circuiting. Literal sets are finite and nonempty; integer identities and coordinates exclude booleans.

| Kind | Arguments and meaning |
|---|---|
| `all`, `any` | `predicates`: validated child predicates. |
| `tile_matches` | `tiles`, `fields`: at least one accepted tile has all declared final ownership/resource/improvement/pillage/food-yield values or bounds. No tool/worker constraint. |
| `charged_builder_at` | `tiles`: an owned living builder occupies a preregistered legal eligible tile with charges > 0. Eligibility includes frozen tech/ownership assertions; remaining moves may be zero. |
| `active_production` | `cities`, `items`, `repair`, optional `tiles`: verified active queue matches an allowed commitment. |
| `housing_resolved` | `cities`, `remedy_buildings`, `minimum_surplus`: completed accepted remedy and measured housing minus population meets the frozen bound. |
| `district_committed` | `cities`, `district_types`, `tiles`: accepted placement and corresponding active construction both hold. |
| `target_damaged` | `target`, `minimum_damage`: verified live target HP decrease reaches the frozen threshold. |
| `target_neutralised` | `target`: captured lifecycle evidence proves destruction, not disappearance from vision. |
| `civilian_covered` | `unit`, `tiles`: civilian is alive at an accepted final tile and shared cover holds. Tactical rescue binds destination tiles excluding its starting tile. |
| `unit_in_area` | `unit_types`, `tiles`: an owned eligible reinforcement exists in the defended area. Initially false is enforced by rubric validation. |
| `unit_lost` | `unit`: owned asset existed before and is verifiably lost afterward; known consumption/transformation excludes loss. |
| `asset_displaced` | `tiles`, `asset_fields`: declared initial productive asset is irreversibly removed/replaced in a before/after transition. |
| `new_civilian_exposure` | `unit`: living initially unexposed civilian is exposed at the final endpoint. |

`unit_lost` requires a transition and calls `classify_lifecycle`. The classifier combines complete own-unit capture with recorded operation arguments and verified outcome evidence; it cannot infer destruction merely from a missing row when consumption or upgrade is unresolved. A successful final-charge improvement with the intended tile outcome and a disappeared one-charge builder is verified consumption. An upgrade requires the matching replacement identity/type and verified operation. A combat loss requires the battle/result and complete entity evidence. Rejection-shaped text alone cannot erase an observed mutation. Unknown cases remain unresolved: a declared harm depending on them raises an evidence error, while the audit reports the gap. These derived classifications remain outside canonical state/digests. Scored tactical targets likewise require independently verified status.

```python
def newly_exposed(initial, final, ref):
    # Complete own-unit evidence establishes whether the civilian still exists;
    # the cause of absence is handled separately by lifecycle classification.
    if not require_living_civilian(final, ref):
        return False
    return not civilian_exposed(initial, ref) and civilian_exposed(final, ref)
```

Define `require_living_civilian(state, ref) -> bool` and `newly_exposed(initial, final, ref) -> bool` as private module helpers. The former returns false for verified absence from a complete own-unit capture and raises for incomplete coverage or an extant entity with unknown role/health. It does not label the cause of disappearance. Shared cover is owned-city occupancy or owned military distance ≤ 1; exposure additionally requires a currently visible hostile combat unit at distance 1 and no cover. Reuse `_hex_distance` without altering its established convention. Initial and final are the episode boundaries for new exposure, and consecutive step boundaries plus `transition` are used for irreversible losses. No second tactical geometry or distance-credit predicate.

- [ ] **Run lifecycle/predicate tests and `tests/arena/test_action_metrics.py`.** Include unknown kind in an unvisited `any` branch, absent visibility versus explicit false, no positive rung for simply moving closer, and no destruction claim for unresolved disappearance.
- [ ] **Commit the Task 4 files and fingerprint update:** `git commit -m "feat(benchmark): add endpoint and civilian exposure predicates"`.

## Task 5: Derive signed scores, event deductions, and progress attribution

**Files:**
- Create: `src/civ_mcp/arena/benchmark_scoring_v2.py`.
- Test: `tests/arena/test_benchmark_scoring_v2.py`.
- Modify: `src/civ_mcp/arena/benchmark_manifest_v2.py` to call rubric validation.

**Interfaces:**
- Produces `validate_rubric(rubric: dict[str, Any], initial: dict[str, Any]) -> None`, `score_trial(trial: dict[str, Any], rubric: dict[str, Any]) -> dict[str, Any]`, `attribute_progress(trial: dict[str, Any], rubric: dict[str, Any]) -> list[dict[str, Any]]`.
- Objective record: `id`, `rungs: [{points, predicate}]`. Harm record: `id`, `loss_key`, `objective_id`, `weight`, `weight_reason`, `timing` (`final` or `event`), `predicate`, `compensation: [{timing, predicate}]`, and explicit integer `priority`. Compensation timing is `event` or `final`; every evaluated event predicate receives its raw transition. Each loss key binds a finite named asset/event scope.
- Score result: per-objective credit/evidence, fired/compensated/deduplicated harms/evidence, `gross_credit`, `harm_total`, `net_credit`, `maximum_credit`, `maximum_harm`, `primary_score`, and increment derivation.

- [ ] **Write numeric and event-history tests.** A one-objective fixture is allowed in unit tests; final Part 1 position admission enforces three × four. Include zero/NaN/negative weights, unknown references, initially true rungs, overlapping loss keys without precedence, and maximum loss beyond positive maximum.

```python
from civ_mcp.arena.benchmark_scoring_v2 import signed_totals

def test_harm_only_is_below_null_without_clipping():
    assert signed_totals([0, 0, 0], [4], 12) == {
        "gross_credit": 0, "harm_total": 4, "net_credit": -4,
        "maximum_credit": 12, "primary_score": -1 / 3,
    }
    assert signed_totals([4, 2, 0], [4], 12)["primary_score"] == 1 / 6
```

- [ ] **Run `uv run pytest tests/arena/test_benchmark_scoring_v2.py -q`; confirm failure.**
- [ ] **Implement highest-rung endpoint credit and event-aware harm evaluation.** Validate the whole rubric before any predicate evaluation. Define the tested helper as follows after rejecting booleans/nonfinite values and invalid ranges:

```python
def signed_totals(credits, deductions, maximum):
    gross = sum(credits)
    harm = sum(deductions)
    return {"gross_credit": gross, "harm_total": harm, "net_credit": gross - harm,
            "maximum_credit": maximum, "primary_score": (gross - harm) / maximum}
```

For each objective, evaluate all rungs against initial/final and take the greatest satisfied value. For each `event` harm, inspect every recorded before/after transition, including an error-returning mutation; retain the original lost entity/event reference. For `final` harm, compare episode initial/final. Evaluate frozen compensation on its declared endpoint/transition and store supporting facts even when it suppresses a deduction. Group by `loss_key`, choose the highest declared priority once, reject priority ties that could change weight, and bound the sum of each group's maximum weight before execution. Do not charge missed progress.

`attribute_progress` uses the same rung predicates on consecutive recorded states and emits objective IDs whose attained credit increases; undo can reduce endpoint credit but never erase the audit trail. Return step indices and measured before/after objective credit, never use attributed step count as primary score. Record `2/12` and `4/12` interpretation scales, penalty maxima, and attainable values from declared rungs; preserve parent advancement thresholds.

- [ ] **Run scoring and predicate tests.** Require tests for harm→later queue overwrite, repeated loss observations, loss plus completion, complete improvement with consumed builder, temporary exposure repaired, exposed null, accepted compensation, and simultaneous full score. Mutate an expectation file in an integration fixture later; it must never change this function's output.
- [ ] **Commit the Task 5 files and fingerprint update:** `git commit -m "feat(benchmark): score progress and deduplicated harm separately"`.

## Task 6: Build one ledger/classifier and reproduce the historical audit offline

**Files:**
- Create: `src/civ_mcp/arena/benchmark_ledger.py`, `src/civ_mcp/arena/benchmark_audit.py`, `tests/arena/fixtures/builder_uncredited_audit_v1.json`, `tests/arena/fixtures/builder_uncredited_audit_v1_inputs.json.gz`.
- Test: `tests/arena/test_benchmark_ledger.py`, `tests/arena/test_benchmark_audit.py`.
- Read: [historical audit](../../research/arena-benchmark-builder-calibration-uncredited-actions-audit.md), archived v1/v2 builder-economy trials, their frozen manifests, and `action_metrics.py` at `bf0f0b5`.

**Interfaces:**
- Consumes Task 4's `classify_lifecycle(step)` for both loss coverage and charge consumption; do not implement a second lifecycle classifier in the ledger.
- Produces `build_ledger(trial: dict[str, Any]) -> dict[str, Any]` in `benchmark_ledger`.
- Produces `mutation_records(trial: dict[str, Any], progress: list[dict[str, Any]], *, task_tiles: list[tuple[int, int]]) -> list[dict[str, Any]]`, `classify_mutation(record: dict[str, Any]) -> dict[str, Any]`, `audit_losses(trial: dict[str, Any], declared_losses: list[dict[str, Any]]) -> list[dict[str, Any]]`, `reproduce_audit(fixture_path: Path, *, root: Path) -> dict[str, Any]` in `benchmark_audit`.
- Classification has an exclusive `category` plus descriptive `tags` and raw evidence references. Categories: `declared_harm`, `undeclared_loss`, `economic_change`, `builder_positioning`, `non_builder_movement`, `insufficient_evidence`. Historical labels are general tags, not a mode switch.

- [ ] **Write ledger and loss-coverage tests.** Verify improve/undo has zero net tile delta, city-total drift alone is ignored, unavailable yield ≠ zero, and losses on credited/error-returning actions remain visible. Legitimate last charge and verified upgrade are lifecycle transformations; unresolved disappearance stays insufficient evidence.

```python
from civ_mcp.arena.benchmark_audit import classify_mutation

def test_deleted_unscored_asset_has_a_report_bucket():
    record = {"step": 3, "declared_harm_ids": [], "objective_ids": [],
              "losses": [{"kind": "unit", "entity": [0, 9],
                          "lifecycle": "destroyed", "declared": False}],
              "economic_changes": [], "movement": None,
              "coverage": {"loss": "complete"}}
    result = classify_mutation(record)
    assert result["category"] == "undeclared_loss"
    assert result["primary_deduction"] == 0
```

- [ ] **Run `uv run pytest tests/arena/test_benchmark_ledger.py tests/arena/test_benchmark_audit.py -q`; confirm failure.**
- [ ] **Implement raw deltas and the classifier.** Compare measured values by stable entity/tile key, record units/coverage/source steps, and keep net initial/final separately from per-step changes. Calculate six yields only on changed tiles, resource access, balances, and charges; separate observable harvest receipts from recurring yields. Unknown lifecycle or required measurement is unresolved. Category precedence is declared harm, undeclared loss, economic change, builder positioning, other movement, insufficient evidence; retain all secondary tags and loss references so precedence hides nothing.

```python
def measured_delta(before, after):
    if before is None or after is None:
        return {"before": before, "after": after, "delta": None,
                "coverage": "unavailable"}
    return {"before": before, "after": after, "delta": after - before,
            "coverage": "measured"}
```

Define `measured_delta` in `benchmark_ledger`; do not multiply it by guessed utility weights. `audit_losses` inspects all complete transitions independently of successful/uncredited-action selection. `mutation_records` chooses successful state-changing tool calls without objective-progress attribution for the uncredited section, and cross-links the complete loss audit. Add an under-credit section for achieved declared completions receiving less than full credit.

- [ ] **Construct the expected-membership JSON once from the archived evidence and reviewed historical audit.** Pin raw trial paths/SHA-256, frozen rubric bytes/digest and evaluator revision `bf0f0b5`, the archived player-facing task list and its digest, and all expected `(campaign, block, trial, step, tag)` records. Include the two quarry under-credit records separately. Do not handwave the 117 identities with an aggregate count.

`benchmark_runs/` is ignored by Git. Bundle the complete 96 raw trial JSON texts, frozen rubric texts, and public task-list evidence into the separate deterministic gzip input fixture (`gzip.compress(..., mtime=0)`), each with original path and SHA-256. The two campaigns total about 5.6 MB before compression. Preserve every trial and step, not just expected uncredited members; the input bundle has no expected labels. Hash both the bundle and its embedded original texts. This makes the regression executable from a fresh checkout. Fixture construction and local preflight also compare against the original archived files when present; the regression itself uses the bundled, digest-verified raw evidence.

`reproduce_audit` must independently enumerate every trial/step in the 96 raw trials, recompute historical progress attribution under the pinned v1 interpretation, then pass normalized records to `classify_mutation`. Expected membership is read only for comparison after actual results exist. No expected-list filtering and no `historical=True` classifier branch. Preserve original v1 semantics in the existing compatibility evaluator; if current code differs from `bf0f0b5`, pin the necessary v1 interpretation explicitly with tests rather than changing v2 rules. Archived own-tile improvement and task-distance facts produce the general tags `farm_on_own_tile`, `closer_to_public_task`, `same_distance_to_public_task`, `farther_from_public_task`, and `non_builder_move`. They do not fabricate missing tile yields.

```python
from pathlib import Path
from civ_mcp.arena.benchmark_audit import reproduce_audit

def test_historical_membership_is_recomputed_from_all_raw_trials():
    result = reproduce_audit(
        Path("tests/arena/fixtures/builder_uncredited_audit_v1.json"), root=Path("."))
    assert result["membership_matches"]
    assert result["trial_count"] == 96
    assert result["affected_trial_count"] == 64
    assert result["uncredited_count"] == 117
    assert result["tag_counts"] == {
        "farm_on_own_tile": 28, "closer_to_public_task": 13,
        "same_distance_to_public_task": 36, "farther_from_public_task": 9,
        "non_builder_move": 31,
    }
    assert result["declared_harm_count"] == 0
    assert result["undercredited_completion_count"] == 2
```

Missing original archives block fixture construction; missing or corrupt bundled inputs fail the subsequent regression rather than skipping it. Attach the audit's later city-yield supplement by name/digest with its historical-probe label. It may support interpretation of the farms; raw historical tile deltas remain unavailable.

- [ ] **Run both new test files plus `tests/arena/test_action_metrics.py`.** Deliberately alter one fixture expectation and one raw-input digest in temporary copies; require mismatch/failure, proving the expected list is not selecting the evaluated data. This entire regression is offline and precedes all live clocks.
- [ ] **Commit the Task 6 files and fingerprint update:** `git commit -m "feat(benchmark): report measured benefits and loss coverage"`.

## Task 7: Add a finite scripted backend and direct round-trip counters

**Files:**
- Create: `src/civ_mcp/arena/benchmark_scripted.py`.
- Modify: `src/civ_mcp/arena/benchmark_agent.py`.
- Test: `tests/arena/test_benchmark_scripted.py`, `tests/arena/test_benchmark_agent.py`.

**Interfaces:**
- Produces `ScriptedBackend(script: dict[str, Any], *, game_tools: tuple[str, ...])`, with `async chat(messages, tools) -> Reply` and `assert_exhausted() -> None`.
- Adds version-2 evidence counters: `round_trips`, `round_trips_completed`, `tool_call_attempts`, `dispatched_calls`; every tool step records `round_index`. Add an explicit `evidence_version="1.0.0"` default to the agent so legacy serialization remains unchanged.

- [ ] **Write batching and separation tests.** Construct two batches (two queries, then finish), verify actual dispatch, counters, finish-only accounting, exhaustion failure, unknown tool rejection, and inability to attach expectations to a script.

```python
import pytest
from civ_mcp.arena.benchmark_scripted import ScriptedBackend

@pytest.mark.asyncio
async def test_script_returns_batches_without_scoring_access():
    script = {"schema_version": "2.0.0", "script_id": "observe",
              "batches": [{"calls": [
                  {"name": "get_units", "arguments": {}},
                  {"name": "get_cities", "arguments": {}}]},
                  {"calls": [{"name": "finish_trial", "arguments": {}}]}]}
    backend = ScriptedBackend(script, game_tools=("get_units", "get_cities"))
    from civ_mcp.arena.benchmark_agent import resolved_benchmark_tools
    schemas = resolved_benchmark_tools(("get_units", "get_cities"))
    assert len((await backend.chat([], schemas)).tool_calls) == 2
    assert (await backend.chat([], schemas)).tool_calls[0]["name"] == "finish_trial"
    backend.assert_exhausted()
```

- [ ] **Run `uv run pytest tests/arena/test_benchmark_scripted.py -q`; confirm failure.**
- [ ] **Implement only the backend reply interface and instrumentation.** Convert declared calls to existing `Reply.tool_calls` dictionaries using deterministic IDs `script_id:round:call` and JSON object arguments. Validate the actual passed schema identity, not just constructor names. Reject exhausted scripts rather than fabricating an implicit finish. The backend has no connection, state snapshots, rubric, case expectations, or policy repair.

```python
import json
from civ_mcp.arena.backends import Reply

def batch_reply(script_id, round_index, batch):
    return Reply(text=None, tool_calls=[
        {"id": f"{script_id}:{round_index}:{index}", "name": call["name"],
         "arguments": json.dumps(call["arguments"], sort_keys=True)}
        for index, call in enumerate(batch["calls"])
    ])
```

Define `batch_reply` in `benchmark_scripted`. Internal `Reply` zero-token defaults are transport compatibility only; scripted persisted model-token fields become null in Task 8. Increment `round_trips` immediately before each backend call and completed rounds after a reply, including finish-only/implicit-finish replies; expose unfinished rounds on timeout. Increment attempts for every non-finish emitted call, including rejected names/malformed arguments, and dispatch count only when invoking the registry. Preserve processing of every game call in a finish-containing batch. Lock `max_steps=15` at validation admission, leaving generic agent defaults and Plan 2's eight-step manifest unchanged.

- [ ] **Run scripted and existing agent tests.** Add real `SingleTurnAgent` tests with two calls plus finish in one round, finish in a separate second round, malformed arguments, and step cap. Require `attempts / round_trips` of 2.0 versus 1.0 for equivalent calls in those two batching layouts. No token double-counting across a batch.
- [ ] **Commit the Task 7 files and fingerprint update:** `git commit -m "feat(benchmark): run scripted replies through the benchmark agent"`.

## Task 8: Integrate scripted validation with the real runner and version-2 reports

**Files:**
- Create: `src/civ_mcp/arena/benchmark_validation.py`, `src/civ_mcp/arena/benchmark_report_v2.py`.
- Modify: `src/civ_mcp/arena/benchmark_runner.py`, `src/civ_mcp/arena/benchmark_schedule.py`; narrow version routing in `benchmark_report.py` only if its CLI is reused.
- Test: `tests/arena/test_benchmark_validation.py`, `tests/arena/test_benchmark_report_v2.py`, existing runner/store/report tests.

**Interfaces:**
- Produces `ScriptedTrialSpec(index: int, position_id: str, arm_id: str, script_id: str, case_id: str)` with `model=None`, `seed=None`, `pair_id=None`; runner callbacks accept `TrialSpec | ScriptedTrialSpec`. Legacy schedule generation is untouched.
- Produces `run_validation(suite_path: Path, run_dir: Path) -> Awaitable[dict[str, Any]]`, `check_case(actual: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]` in `benchmark_validation`.
- Produces `build_trial_report(trial: dict[str, Any], position: dict[str, Any]) -> dict[str, Any]`, `render_report(report: dict[str, Any]) -> str`, `validate_model_inputs(trials: list[dict[str, Any]]) -> None` in `benchmark_report_v2`.
- CLI: `uv run python -m civ_mcp.arena.benchmark_validation run --suite PATH --run-dir PATH` and `report --run-dir PATH --output PATH`.

- [ ] **Write integration tests against `BenchmarkRunner`, `SingleTurnAgent`, `BenchmarkStore`, and actual registry dispatch.** Inject fake connection transport or `GameState` method responses at the game boundary; do not replace the agent with a fake score producer or monkeypatch the registry dispatch itself. A `fortify_unit` or `move_unit` dispatch must change the fake world, and before/after snapshots must record that change in a committed trial. Test interrupted attempt/resume with immutable lock identity and no duplicate commit.

```python
import pytest
from civ_mcp.arena.benchmark_report_v2 import validate_model_inputs
from civ_mcp.arena.benchmark_validation import check_case

def test_expected_score_cannot_override_derived_score():
    actual = {"gross_credit": 0, "harm_total": 4, "net_credit": -4,
              "primary_score": -1 / 3}
    result = check_case(actual, {"primary_score": 1.0})
    assert result["passed"] is False
    assert actual["primary_score"] == -1 / 3

def test_scripted_trials_are_rejected_from_model_comparisons():
    with pytest.raises(ValueError, match="scripted"):
        validate_model_inputs([{"schema_version": "2.0.0",
                                "actor_kind": "scripted", "counting": False}])
```

- [ ] **Run `uv run pytest tests/arena/test_benchmark_validation.py tests/arena/test_benchmark_report_v2.py -q`; confirm failures.**
- [ ] **Build the validation factory at `make_agent` and preserve runner/store authority.** Reuse confirmed reload, popup hygiene, initial digest comparison, attempt budget/journal, final capture, and atomic commit. Resolve explicit tool names and v2 capture closure; create a fresh `ScriptedBackend` per attempt. A script rejection, unexpected implicit finish, unconsumed batch, invalid required call, missing capture, turn/active-player drift, or failed endpoint assertion blocks validation. Preserve failed raw evidence rather than converting it to score zero.

```python
def check_case(actual, expected):
    mismatches = {key: {"actual": actual.get(key), "expected": value}
                  for key, value in expected.items() if actual.get(key) != value}
    return {"passed": not mismatches, "mismatches": mismatches}
```

Compare canonical structures and exact integer/fraction-derived expected scores; do not use a loose score tolerance. Expand the function to validate endpoint and ledger assertions through the same finite predicate/measurement vocabulary. Expected data remains a separate input after the raw trial is scored. Error-returning **deliberate rejection** tests must explicitly mark the expected rejection in their case; successful required operations may not be silently dropped.

Version-2 persisted evidence adds actor/script/case identities, contract/toolset/scope identities, counters, capture telemetry, and null model-only fields. The lock includes ordered schedule and raw script/case digests. Resume/report rejects changed scripts, cases, position, archive, toolset, contract or source fingerprint; a run directory rename is not admission. Keep the store's existing atomic byte checks. A model health canary is inapplicable to scripted actors: a script execution timeout is failed validation with game-health evidence, never a model `runaway_timeout` score.

The report combines score, raw ledger, uncredited mutations, all-action loss coverage, under-credit audit, turn/identity validation, truncation, terminal, round/call counters and capture summary. Include calls-per-round per episode and, for eligible later model records, grouped model distribution (n, min, median, p95, max). Script reports show operational timing only. Deterministic reports omit fresh generation timestamps and absolute host-dependent paths; provenance timestamps already locked in input may be rendered. Model aggregation rejects mixed contracts/surfaces, mismatched version/scope identities within a position, and every scripted/non-counting record before computing statistics. Different positions legitimately have different frozen scopes. No implementation of the Part 2 screen is needed.

- [ ] **Run validation/report tests and existing runner, store, agent, position and v1 report tests.** Assert capture timeout writes an infrastructure attempt; no-script model endpoint admission is bypassed; script actor never calls backend probing; altered expected score fails case validation; regenerated JSON and Markdown are byte-identical.
- [ ] **Commit the Task 8 files and fingerprint update:** `git commit -m "feat(benchmark): validate scripts with runner evidence and signed reports"`.

## Task 9: Persist authoring clocks and export immutable native saves

**Files:**
- Create: `src/civ_mcp/arena/benchmark_authoring_journal.py`.
- Modify: `src/civ_mcp/game_launcher.py`, `src/civ_mcp/launcher_cli.py`, `src/civ_mcp/arena/benchmark_deploy.py`.
- Test: `tests/arena/test_benchmark_authoring_journal.py`, `tests/arena/test_benchmark_archive_export.py`, `tests/test_game_launcher.py`, `tests/test_launcher_cli.py` where their platform mocks are already available.

**Interfaces:**
- Produces `AuthoringJournal(path: Path)`, `begin(*, family: str, scenario_id: str, predecessor: str | None, reason: str | None, material_change: str | None) -> dict[str, Any]`, `record_stage(*, scenario_id: str, stage: str, evidence: dict[str, Any]) -> None`, `finish(*, scenario_id: str, passed: bool) -> dict[str, Any]`.
- Adds `export_benchmark_save(name: str, destination: str, *, expected_sha256: str | None = None) -> dict[str, Any]` in the native launcher, and `export_via_windows(name: str, destination: str, expected_sha256: str | None = None) -> dict[str, Any]` in `benchmark_deploy`.
- Native CLI: `export-save --name NAME --destination PATH --json`; source is the Windows Documents save directory discovered by the existing Known Folder helper.

- [ ] **Write clock/restart/substitution and archive safety tests.** Inject clocks so no real waiting is needed. Test process restart does not reset a scenario, a rename is rejected as substitution, exactly one declared material substitute is accepted after failure, second failure blocks, and the family total includes both attempts. Reject backward wall-clock movement rather than shortening elapsed time.

```python
from pathlib import Path
import pytest
from civ_mcp.arena.benchmark_authoring_journal import AuthoringJournal

def test_substitution_requires_a_failed_predecessor(tmp_path):
    journal = AuthoringJournal(tmp_path / "attempts.json")
    journal.begin(family="builder", scenario_id="builder-a1", predecessor=None,
                  reason=None, material_change=None)
    with pytest.raises(ValueError, match="failed predecessor"):
        journal.begin(family="builder", scenario_id="builder-a2", predecessor="builder-a1",
                      reason="different geometry", material_change="move threat to an open route")
```

- [ ] **Run `uv run pytest tests/arena/test_benchmark_authoring_journal.py tests/arena/test_benchmark_archive_export.py -q`; confirm failure.**
- [ ] **Implement durable attempt records and non-overwriting export.** Start before the first live survey/reload, persist UTC start and monotonic elapsed checkpoints, enforce 10,800 elapsed seconds across interruptions and process restarts, and record stage start/end/durations. A recipe revision in the same scenario shares the clock. Do not pause time for debugging or queued operations. Stop at final restored-state verification, not archive creation. Atomic writes and one-process ownership prevent concurrent clock edits.

```python
def elapsed_authoring_seconds(started_unix_s, now_unix_s, recorded_elapsed_s):
    if now_unix_s < started_unix_s + recorded_elapsed_s:
        raise ValueError("authoring clock moved backwards")
    return max(recorded_elapsed_s, now_unix_s - started_unix_s)
```

Define this helper in `benchmark_authoring_journal`; reconcile same-process monotonic elapsed with persisted wall elapsed conservatively. `begin` opens a clock for attempt 1 or the one allowed substitute; repeat calls for the same identity return the existing record. Substitution requires a terminal failed predecessor and a declared material scenario difference before any substitute live command. A third identity is rejected. Retain failed recipe versions, runs, archives and stage evidence.

For save export, accept a basename (spaces permitted for the known base save), reject separators/traversal, obtain the native Windows save path, wait for a complete stable file with a bounded timeout, copy to a temporary destination, verify source/copy hashes and stable source metadata, then atomically create the archive without overwriting any existing version. Existing identical archive is idempotent only after digest verification; mismatching destination fails. Use the existing bridge invocation/path conversion and native `--json` error handling. Do not guess the Linux process's Documents location or read a partially written `Network.SaveGame` result as final.

- [ ] **Run new tests and existing deployment/launcher tests touched by the change.** Use temporary files and platform mocks; all remain offline. Pin traversal rejection, partial-save timeout, same digest idempotence, differing digest refusal, and family-duration accounting.
- [ ] **Commit the Task 9 files and fingerprint update:** `git commit -m "feat(benchmark): retain authoring attempts and export immutable saves"`.

## Task 10: Build the replayable authoring workflow and concrete offline recipes

**Files:**
- Create: `src/civ_mcp/arena/benchmark_authoring.py`, `benchmarks/recipes/plan3-builder-a1.yaml`, `benchmarks/recipes/plan3-city-a1.yaml`, `benchmarks/recipes/plan3-tactical-a1.yaml`.
- Test: `tests/arena/test_benchmark_authoring.py`.
- Modify: `benchmark_manifest_v2.py` for recipe/authoring assertion validation and `benchmark_position.py` only for the injected v2 capture already defined.

**Interfaces:**
- Produces `load_recipe(path: Path) -> dict[str, Any]`, `run_authoring_stage(recipe_path: Path, *, stage: str, attempt_dir: Path) -> Awaitable[dict[str, Any]]`.
- CLI: `uv run python -m civ_mcp.arena.benchmark_authoring STAGE --recipe PATH --attempt-dir PATH`, where `STAGE` is `survey`, `apply`, `probe`, `archive`, `capture`, `verify`, `menu-check`, `validate`, `finish`. Every live stage requires the persistent attempt journal and an unexpired clock.
- Offline CLI: `preflight --recipes PATH... --output PATH` and `gate --packets PATH... --output PATH`. These never connect to the game.

- [ ] **Write a fake-transport stage-order test.** Assert that survey starts the clock before connecting, apply always reloads the identified base, probes dispatch through the frozen tools, archive cannot precede successful readback, verification is exactly twelve cycles, menu-check uses recovery loading, and finish requires every gate plus final restored digest. Failed/expired stages retain evidence and never issue later mutations.

```python
import pytest
from civ_mcp.arena.benchmark_authoring import validate_stage_transition

def test_archive_requires_legality_probe_evidence():
    with pytest.raises(ValueError, match="probe"):
        validate_stage_transition("archive", {"survey": "passed", "apply": "passed"})
```

- [ ] **Run `uv run pytest tests/arena/test_benchmark_authoring.py -q`; confirm failure.**
- [ ] **Implement orchestration around existing operations and commit recipes before live use.** Define `validate_stage_transition(stage: str, completed: dict[str, str]) -> None` in `benchmark_authoring`, with the ordered stages above; survey/apply/probe may repeat under the same clock with every result retained. Repeating an earlier stage invalidates downstream certificates; changed bindings require a new immutable archive/position version and complete revalidation under the same scenario clock. Recipe fields: schema/recipe/scenario/family/version, base identity/hash, player, setup operations and assertions, survey queries, binding rules, coverage rule, proposed objectives/harms, toolset, probes, archive name/path, packet paths, script/case definitions and expected results. Include `predecessor`, `substitution_reason`, and `material_change` (null for attempt1, all required for a substitute); pass these to `AuthoringJournal.begin` before its first live command. Validate raw authoring Lua separately from script tool calls; it cannot appear in an episode.

```python
STAGE_PREREQUISITES = {
    "survey": (), "apply": ("survey",), "probe": ("apply",),
    "archive": ("probe",), "capture": ("archive",), "verify": ("capture",),
    "menu-check": ("verify",), "validate": ("menu-check",), "finish": ("validate",),
}

def validate_stage_transition(stage, completed):
    for prerequisite in STAGE_PREREQUISITES[stage]:
        if completed.get(prerequisite) != "passed":
            raise ValueError(f"{stage} requires successful {prerequisite}")
```

Use the identified organic base recorded in `benchmarks/provenance/builder-posctrl-v1-authoring.json`: `SEONDEOK 100 400 BC`, SHA-256 `2cd485b005cb2afe2d58ceaac60be56dd80ea3d5ccc66f6796427d9057c2ab29`. Resolve/export and verify it through the native bridge; do not reuse a retired calibration scenario as a new position. Recipe bindings use names plus measured selection constraints and resolve to concrete owner/ID/coordinates in the recorded survey. Ambiguous or missing bindings fail with candidate evidence. Concrete tile/entity IDs, damage threshold and acceptable sites become committed authoring output after tool legality probes, never guessed constants in this plan.

Workflow details:

1. Survey only through the proposed public tools, saving raw results for candidate discoverability. Also obtain the private complete evidence needed for setup verification, keeping it out of the observation record.
2. Apply from the hashed base every time. Record exact Lua setup requests/results and resolved entities. Use existing verified setup patterns (`UnitManager.PlaceUnit`, `UnitManager.RestoreMovement`, `ImprovementBuilder.SetImprovementPillaged`) where applicable; prove any additional setup operation by readback. No raw mutation is allowed after the scenario archive is frozen for scripts.
3. Probe every required endpoint/harm/compensation through registry tools on disposable setup copies, recording actual results and restoring the declared start between probes. Do not call speculative remote `CanStartOperation` checks that bypass the established unit-action path. Avoid the unresolved camp-on-ivory case.
4. Reapply the recipe from base after probes, assert the intended initial state, call existing `game_lifecycle.save_game`, export a new immutable archive, then call `capture_position` with the v2 adapter. A source-save acknowledgment alone is insufficient evidence of a complete archive.
5. Freeze the immutable `-authoring.json` input, then materialize the strict position, scripts and separate cases, run `verify_position(..., cycles=12)`, and test the existing crash-recovery menu load against that exact archive. Persist hashes and reload confirmation, not just a claimed menu success. Final packet/journal timing data is separate from the already hashed authoring input.
6. Run the locked validation suite through Task 8, regenerate reports twice, restore the scenario archive, verify its digest, and finish the authoring clock. Admission checks the whole evidence packet; a family cannot pass on separate incompatible versions of its witnesses.

Each recipe proposes the objective/penalty shapes from spec section 9. Builder has navigation, scarce usable charges, alternative work and route exposure (harm maximum 8); city has jointly feasible housing remedy/district/repair (harm maximum 4); tactical has already-hostile visible attacker/civilian rescue/reinforcement (harm maximum 4). All positive rungs must be false initially; joint full-score witness and a materially different alternative must both fit fifteen rounds. Recipes include an observation-only discoverability batch sequence and endpoint/harms case tags before live use. Live bindings refine those declared contracts, with all revisions journaled.

- [ ] **Run the authoring tests and position/deploy tests.** Use a fake native bridge and fake world; assert archived expected scores cannot be read by the backend, failed probes prevent archive admission, and replay starts from base rather than the previous mutated state.
- [ ] **Commit the Task 10 files and fingerprint update:** `git commit -m "feat(benchmark): add replayable position authoring workflow"`.

## Task 11: Freeze the candidate contract and pass the offline preflight

**Files:**
- Create: `benchmarks/contracts/instrument-v2.yaml`, `benchmarks/contracts/instrument-v2.md`, `benchmarks/contracts/plan3-part1-budget.yaml`, `benchmarks/provenance/plan3-part1-offline-preflight.json`.
- Test: `tests/arena/test_benchmark_part1_gate.py`.
- Modify: `benchmark_authoring.py` for the offline gate only; do not connect to FireTuner in this task.

**Interfaces:**
- Produces `check_part1_packet(packet: dict[str, Any]) -> dict[str, Any]` and `check_part1_gate(packets: list[dict[str, Any]], preflight: dict[str, Any]) -> dict[str, Any]` in `benchmark_authoring`.
- Preflight binds code/scorer/toolset identities, test results, historical input/report digests and recipe versions. Gate output names failed requirements and source evidence paths, never a silent Boolean with no reason.

- [ ] **Write table-driven gate tests.** Begin with a complete synthetic packet, remove one required piece at a time (twelve checks, menu verification, alternative full score, harm negative case, null digest, capture timing, final restore, failed-attempt history, offline audit, no-model provenance); each removal must fail for that reason. A passing substitute includes both scenario durations.

```python
from civ_mcp.arena.benchmark_authoring import check_part1_packet

def test_null_script_without_capture_timings_cannot_pass():
    result = check_part1_packet({"position_id": "incomplete", "null": {
        "gross_credit": 0, "harm_total": 0, "primary_score": 0,
        "initial_digest": "same", "final_digest": "same", "capture_records": []}})
    assert not result["passed"]
    assert "null_capture_timing" in result["failed_requirements"]
```

- [ ] **Run `uv run pytest tests/arena/test_benchmark_part1_gate.py -q`; confirm failure.**
- [ ] **Implement the gate directly from spec section 11 and freeze the budget record.** Check every before/after and adjacent-boundary digest in the null script, not just its endpoints. Require actual public observation calls and the discoverability assertions; a finish-only script does not satisfy this case. Require complete full-scope capture records all ≤2.0 seconds. No undefined yield/lifecycle/queue values may support a primary or compensation predicate. Required live cases and offline robustness tests must be distinguished in the packet; schema-error fixtures do not need live reloads.

```yaml
schema_version: 2.0.0
budget_id: plan3-part1-v1
unit: backend_round_trip
max_steps: 15
script_episode_wall_s: 300
capture_wall_s: 2.0
model_episode_wall_formula: "max(300, ceil(15 * p95_roundtrip_s * 1.5))"
primary_comparable_to_plan2: false
benefit_ledger_gates_advancement: false
authoring_seconds_per_scenario: 10800
scenario_substitutions_per_family: 1
```

In the accompanying contract Markdown cite the parent fifteen-round baseline and Plan 2 v3 evidence (Qwen cap 4/12; batching roughly two calls/round versus Gemma one), preserving the distinction between evidence supporting restoration and proof of an optimal budget. List v2 predicate/report schema, all fingerprint dependencies, finite penalty rules, coverage and toolset identities. Mark this as the candidate contract used for scripted authoring, then release it at Task 15 only if all gates pass. A code/contract amendment during live work is prospective, retains prior evidence, and forces affected packet revalidation under the new identity; it does not reset scenario time.

- [ ] **Run the focused full benchmark regression once.** Run `uv run pytest tests/arena/test_benchmark_*.py tests/arena/test_action_metrics.py -q`, covering new files plus v1. Run `uv run python -m civ_mcp.arena.benchmark_authoring preflight --recipes benchmarks/recipes/plan3-builder-a1.yaml benchmarks/recipes/plan3-city-a1.yaml benchmarks/recipes/plan3-tactical-a1.yaml --output benchmarks/provenance/plan3-part1-offline-preflight.json`. Expect passing runner/dispatch integration, offline 117 membership/counts, strict no-model scripts, and zero game/network calls during preflight. Record actual test results; never copy historical pass counts.
- [ ] **Commit the candidate contract, gate, preflight and tests:** `git commit -m "feat(benchmark): preregister Part 1 budget and acceptance gate"`.

## Task 12: Author and validate the builder-economy position

**Files:**
- Finalize: `benchmarks/recipes/plan3-builder-a1.yaml`.
- Create: `benchmarks/positions/plan3-builder-a1-v1.yaml`, `benchmarks/saves/plan3-builder-a1-v1.Civ6Save`, `benchmarks/provenance/plan3-builder-a1-v1-authoring.json`, `benchmarks/provenance/plan3-builder-a1-v1.json`, `benchmarks/scripts/plan3-builder-a1-v1/`, `benchmarks/validation/plan3-builder-a1-v1/`.
- Evidence: `benchmark_runs/plan3-part1/builder-a1/`, including retained attempts and raw scripts.

**Interfaces:** Consumes the Task 10 stage CLI and Task 11 passing preflight; produces a packet accepted by `check_part1_packet`. The alternate allowed scenario is `builder-a2` with a declared material change and independent immutable versions.

- [ ] **Prepare live ownership following the operating playbook, then run survey.** Record operator/FireTuner ownership before connecting. No model endpoint or credentials are needed. The CLI starts the three-hour clock before its first game command.

```bash
uv run python -m civ_mcp.arena.benchmark_authoring survey --recipe benchmarks/recipes/plan3-builder-a1.yaml --attempt-dir benchmark_runs/plan3-part1/builder-a1
```

- [ ] **Resolve bindings and run apply/probe.** Require builders initially away from eligible work tiles, navigation with enough same-turn movement, scarce but sufficient total charges, repair/resource/food tasks jointly attainable, a non-scoring alternative gain, and a visible hostile route threat. Define accepted equivalent sites from public facts before scripts test them. Probe escort loss versus legitimate action and uncovered versus covered civilian endpoints with the shared geometry. Avoid camp dependence.

```bash
uv run python -m civ_mcp.arena.benchmark_authoring apply --recipe benchmarks/recipes/plan3-builder-a1.yaml --attempt-dir benchmark_runs/plan3-part1/builder-a1
uv run python -m civ_mcp.arena.benchmark_authoring probe --recipe benchmarks/recipes/plan3-builder-a1.yaml --attempt-dir benchmark_runs/plan3-part1/builder-a1
```

- [ ] **Freeze concrete script/case bindings before validation and run archive/capture.** Cases are declarative batches of the frozen tools; expect full credit 12, harm-only −4/12, two distinct four-point harms with maximum8, and each partial objective2. Completion accepts any eligible builder and retains final-charge outcomes. Mixed/undo cases derive exact totals from their declared endpoint states.

Required builder live case tags: `null_discovery`, `joint_full`, `alternative_full`, `partial_repair`, `partial_resource`, `partial_food`, `closer_only`, `escort_loss`, `escort_legitimate`, `new_exposure`, `covered_route`, `temporary_exposure_repaired`, `mixed_gain_loss`, `harm_only`, `repeat_undo`, `final_charge`. Several tags may share a trajectory when all assertions remain visible; do not skip a condition to hit an estimated episode count. The initially-exposed null rule is covered by the offline geometry tests and the tactical live null; the builder's live starting state must remain initially unexposed so its new-exposure harm is testable. Do not mutate a trial's initial state to manufacture another case.

```bash
uv run python -m civ_mcp.arena.benchmark_authoring archive --recipe benchmarks/recipes/plan3-builder-a1.yaml --attempt-dir benchmark_runs/plan3-part1/builder-a1
uv run python -m civ_mcp.arena.benchmark_authoring capture --recipe benchmarks/recipes/plan3-builder-a1.yaml --attempt-dir benchmark_runs/plan3-part1/builder-a1
```

- [ ] **Run twelve reload checks and menu-path verification.** Stop on a mismatch/unconfirmed reload and retain evidence.

```bash
uv run python -m civ_mcp.arena.benchmark_authoring verify --recipe benchmarks/recipes/plan3-builder-a1.yaml --attempt-dir benchmark_runs/plan3-part1/builder-a1
uv run python -m civ_mcp.arena.benchmark_authoring menu-check --recipe benchmarks/recipes/plan3-builder-a1.yaml --attempt-dir benchmark_runs/plan3-part1/builder-a1
```

- [ ] **Run the suite, regenerate reports, and restore before finishing the clock.** The null script must show zero gross/harm/primary, identical complete snapshot chain, full coverage and each capture ≤2.0s. Examine undeclared-loss coverage, including an unscored asset loss in the offline classifier fixtures; do not add a tool to manufacture a live deletion case.

```bash
uv run python -m civ_mcp.arena.benchmark_authoring validate --recipe benchmarks/recipes/plan3-builder-a1.yaml --attempt-dir benchmark_runs/plan3-part1/builder-a1
uv run python -m civ_mcp.arena.benchmark_authoring finish --recipe benchmarks/recipes/plan3-builder-a1.yaml --attempt-dir benchmark_runs/plan3-part1/builder-a1
```

- [ ] **Commit the complete packet if admitted, or the failure record if not.** Use `git commit -m "feat(benchmark): author scripted builder economy position"` for a pass; an archive alone is not task completion. If a1 fails, record terminal failure and prospective material substitution before `builder-a2`'s first live command; execute the entire same gate on a2, retain both attempts, and report family cost. Do not repeat a failed scenario under a fresh name.

## Task 13: Author and validate the city-planning position

**Files:**
- Finalize: `benchmarks/recipes/plan3-city-a1.yaml`.
- Create: `benchmarks/positions/plan3-city-a1-v1.yaml`, `benchmarks/saves/plan3-city-a1-v1.Civ6Save`, `benchmarks/provenance/plan3-city-a1-v1-authoring.json`, `benchmarks/provenance/plan3-city-a1-v1.json`, `benchmarks/scripts/plan3-city-a1-v1/`, `benchmarks/validation/plan3-city-a1-v1/`.
- Evidence: `benchmark_runs/plan3-part1/city-a1/`.

**Interfaces:** Consumes the frozen surface and authoring CLI; produces an accepted city packet. Only one material `city-a2` substitution is allowed.

- [ ] **Start the city clock with survey.** Discover housing, production options, district candidates and urgent repair through `get_cities`, `get_city_production`, `get_district_advisor`, map and purchase queries. Save the discovery evidence proving an objective-blind candidate generator could see these facts.

```bash
uv run python -m civ_mcp.arena.benchmark_authoring survey --recipe benchmarks/recipes/plan3-city-a1.yaml --attempt-dir benchmark_runs/plan3-part1/city-a1
```

- [ ] **Bind compatible cities/commitments and probe all outcomes.** Use a housing remedy that can actually complete via an allowed same-turn action, enough treasury for both joint-full witnesses, multiple acceptable district placements considering displaced assets, and a distinct urgent repair. Queue housing earns2; completed remedy plus resolved shortfall4; suitable active district4; active repair4. An irreversible unacceptable displacement costs4 even after a later queue overwrite; acceptable productive replacement must not fire it.

```bash
uv run python -m civ_mcp.arena.benchmark_authoring apply --recipe benchmarks/recipes/plan3-city-a1.yaml --attempt-dir benchmark_runs/plan3-part1/city-a1
uv run python -m civ_mcp.arena.benchmark_authoring probe --recipe benchmarks/recipes/plan3-city-a1.yaml --attempt-dir benchmark_runs/plan3-part1/city-a1
```

- [ ] **Bind cases and create the archive/canonical state.** Required tags: `null_discovery`, `joint_full`, `alternative_full`, `housing_partial`, `uncredited_preparation`, `destructive_placement`, `accepted_replacement`, `mixed_gain_loss`, `harm_only`, `queue_overwrite`, `repeat_undo`. Use the offline shared geometry cases for exposure conditions that do not occur in this family; do not introduce an unrelated civilian objective merely to repeat a unit fixture live.

```bash
uv run python -m civ_mcp.arena.benchmark_authoring archive --recipe benchmarks/recipes/plan3-city-a1.yaml --attempt-dir benchmark_runs/plan3-part1/city-a1
uv run python -m civ_mcp.arena.benchmark_authoring capture --recipe benchmarks/recipes/plan3-city-a1.yaml --attempt-dir benchmark_runs/plan3-part1/city-a1
```

- [ ] **Complete twelve reload cycles and the recovery menu path.**

```bash
uv run python -m civ_mcp.arena.benchmark_authoring verify --recipe benchmarks/recipes/plan3-city-a1.yaml --attempt-dir benchmark_runs/plan3-part1/city-a1
uv run python -m civ_mcp.arena.benchmark_authoring menu-check --recipe benchmarks/recipes/plan3-city-a1.yaml --attempt-dir benchmark_runs/plan3-part1/city-a1
```

- [ ] **Validate, regenerate and restore.** Require null timing/digest stability, actual queue readback, no assumed future construction outcome, a twelve-point joint maximum, penalty maximum4, and tile-based benefit/loss evidence rather than citizen-assignment-dependent city yield totals.

```bash
uv run python -m civ_mcp.arena.benchmark_authoring validate --recipe benchmarks/recipes/plan3-city-a1.yaml --attempt-dir benchmark_runs/plan3-part1/city-a1
uv run python -m civ_mcp.arena.benchmark_authoring finish --recipe benchmarks/recipes/plan3-city-a1.yaml --attempt-dir benchmark_runs/plan3-part1/city-a1
```

- [ ] **Commit admitted packet or retained failure:** `git commit -m "feat(benchmark): author scripted city planning position"` on success. A failed a1 may use one declared material a2 substitution with the full gate and fresh three-hour clock; a second failure blocks the family.

## Task 14: Author and validate the tactical-defense position

**Files:**
- Finalize: `benchmarks/recipes/plan3-tactical-a1.yaml`.
- Create: `benchmarks/positions/plan3-tactical-a1-v1.yaml`, `benchmarks/saves/plan3-tactical-a1-v1.Civ6Save`, `benchmarks/provenance/plan3-tactical-a1-v1-authoring.json`, `benchmarks/provenance/plan3-tactical-a1-v1.json`, `benchmarks/scripts/plan3-tactical-a1-v1/`, `benchmarks/validation/plan3-tactical-a1-v1/`.
- Evidence: `benchmark_runs/plan3-part1/tactical-a1/`.

**Interfaces:** Consumes shared cover/hostility/lifecycle predicates and authoring CLI; produces an accepted tactical packet. Only one material `tactical-a2` substitution is allowed.

- [ ] **Start the tactical clock with survey and bind existing hostilities.** Use visible enemies already at war. Verify the damage/destruction evidence, legal attack geometry, exposed civilian, accepted covered endpoints and reinforcement options. Do not make war declaration synchronization or an AI interturn action part of the score.

```bash
uv run python -m civ_mcp.arena.benchmark_authoring survey --recipe benchmarks/recipes/plan3-tactical-a1.yaml --attempt-dir benchmark_runs/plan3-part1/tactical-a1
```

- [ ] **Apply/probe and freeze the tactically meaningful damage threshold from readback.** Require two different jointly achievable full-score paths. Partial damage2 must represent a useful intermediate outcome, verified neutralization4; civilian rescue4 uses present shared cover; reinforcement queued2 or in play in defended area4. Probe uncompensated loss and accepted tactical compensation, anchored at4 to its served objective. Ordinary damage costs zero.

```bash
uv run python -m civ_mcp.arena.benchmark_authoring apply --recipe benchmarks/recipes/plan3-tactical-a1.yaml --attempt-dir benchmark_runs/plan3-part1/tactical-a1
uv run python -m civ_mcp.arena.benchmark_authoring probe --recipe benchmarks/recipes/plan3-tactical-a1.yaml --attempt-dir benchmark_runs/plan3-part1/tactical-a1
```

- [ ] **Bind cases and archive/capture.** Required tags: `null_discovery`, `joint_full`, `alternative_full`, `meaningful_damage`, `reinforcement_partial`, `closer_only`, `covered_rescue`, `initial_exposure_null`, `military_loss`, `accepted_compensation`, `mixed_gain_loss`, `harm_only`, `repeat_undo`. Visibility loss without verified destruction is an offline negative predicate/runner case and, where reachable, an explicitly tagged live witness. Record actual observations, never a model's combat estimate as damage evidence.

```bash
uv run python -m civ_mcp.arena.benchmark_authoring archive --recipe benchmarks/recipes/plan3-tactical-a1.yaml --attempt-dir benchmark_runs/plan3-part1/tactical-a1
uv run python -m civ_mcp.arena.benchmark_authoring capture --recipe benchmarks/recipes/plan3-tactical-a1.yaml --attempt-dir benchmark_runs/plan3-part1/tactical-a1
```

- [ ] **Run twelve verified reloads and the recovery menu path.**

```bash
uv run python -m civ_mcp.arena.benchmark_authoring verify --recipe benchmarks/recipes/plan3-tactical-a1.yaml --attempt-dir benchmark_runs/plan3-part1/tactical-a1
uv run python -m civ_mcp.arena.benchmark_authoring menu-check --recipe benchmarks/recipes/plan3-tactical-a1.yaml --attempt-dir benchmark_runs/plan3-part1/tactical-a1
```

- [ ] **Validate, regenerate and restore.** Null exposure must stay zero; relocation gains progress only at a declared endpoint; loss events survive later actions and cannot duplicate one asset's debit. Require penalty maximum4, full score12, complete identity/lifecycle evidence and capture timings.

```bash
uv run python -m civ_mcp.arena.benchmark_authoring validate --recipe benchmarks/recipes/plan3-tactical-a1.yaml --attempt-dir benchmark_runs/plan3-part1/tactical-a1
uv run python -m civ_mcp.arena.benchmark_authoring finish --recipe benchmarks/recipes/plan3-tactical-a1.yaml --attempt-dir benchmark_runs/plan3-part1/tactical-a1
```

- [ ] **Commit admitted packet or retained failure:** `git commit -m "feat(benchmark): author scripted tactical defense position"` on success. Use the one declared a2 substitution only for a materially changed scenario, with all failed evidence retained; second failure blocks Part 1.

## Task 15: Evaluate the exit gate and publish the Part 1 evidence record

**Files:**
- Create: `docs/research/arena-benchmark-plan-3-part-1-exit.md`, `benchmarks/provenance/plan3-part1-exit.json`.
- Finalize release record: `benchmarks/contracts/instrument-v2.md`; verify the already frozen `benchmarks/contracts/instrument-v2.yaml` without changing its identity.
- Update status links: this plan, the [Part 1 spec](../specs/2026-10-08-arena-benchmark-plan-3-part-1-design.md), and the [handoff](2026-10-08-plan-3-brainstorm-handoff.md).

**Interfaces:** Consumes the three admitted packet JSON paths emitted by `finish`, plus offline preflight; produces a machine-readable gate result and human-readable evidence report. If a substitute passed, use its packet path and retain its failed predecessor in the input provenance.

- [ ] **Run the offline aggregate gate against the actual final versions.** The command below is for three a1 passes; substitute only the corresponding real a2 packet path where the journal records that authorized substitution.

```bash
uv run python -m civ_mcp.arena.benchmark_authoring gate --packets benchmarks/provenance/plan3-builder-a1-v1.json benchmarks/provenance/plan3-city-a1-v1.json benchmarks/provenance/plan3-tactical-a1-v1.json --output benchmarks/provenance/plan3-part1-exit.json
```

Expect all named requirements passed, three family packets with matching code/tool/contract identity, actual per-scenario duration ≤10,800s, family totals including failures, offline117 regression passed, complete null stability/timing evidence, and no tested-model library transcript. A failing gate keeps Part 1 open; documentation must name the unmet requirement.

- [ ] **Verify report reproducibility from retained raw evidence.** Generate each report twice into separate temporary outputs and compare bytes; verify archive, raw evidence, script, expected-case, toolset, scorer and provenance hashes. No live reload is needed for report regeneration. Run additional software tests only if code changed after preflight, and invalidate/revalidate affected live packets when their identity changed.
- [ ] **Write the exit report with actual evidence and release the contract only on pass.** Include per-objective/harm results, negative/compensated cases, ledger and undeclared-loss coverage, null chain and timing summaries, authoring stage/family costs, classifier membership/counts, and the restored-budget derivation. Distinguish scripted mechanics validation from model decision quality. Release records name precise schema/source/toolset identities, not an unqualified “v2 passed.”
- [ ] **Update only roadmap deltas.** Remaining work: author the other three development and three held-out positions, extend the common tools under a new identity if needed and revalidate all affected positions, freeze all nine together, admit the five-model roster, derive per-position interpretation scales, then use the parent's unchanged screen/advancement/Stage 3/Stage 4 rules. Briefing/playbook are separate injections; tracker remains multi-turn; decision-model work requires a separate same-menu-control design. No model pilot is authorized by this task.
- [ ] **Commit the gate/exit/status record:** `git commit -m "docs(benchmark): record Plan 3 Part 1 exit evidence"`. Do not mark this task or Part 1 complete when any gate remains failed.

## Plan review and coverage

This plan is the implementation handoff; unchecked tasks are intentional. No software or live position work is claimed here.

| Spec requirement | Implementation tasks |
|---|---|
| Existing runner/capture seams and separate scripted actor | 2, 3, 7, 8 |
| Frozen 35-tool list and explicit schema/source identities | 1, 8, 10, 11 |
| Endpoint credit, signed harm, anchored weights and deduplication | 4, 5 |
| Shared cover/new exposure; null stays zero | 4, 5, 11, 12, 14 |
| Tile ledger, all-action undeclared loss, historical117 offline fixture | 6, 8, 11 |
| Two-second capture bound and episode-deadline attribution | 3, 8, 11–14 |
| Three-hour authoring, one material substitution, retained failures | 9, 10, 12–15 |
| Fifteen rounds, batching attribution, Plan 2 evidence | 7, 8, 11, 15 |
| Three jointly feasible position contracts and scripted exit gate | 10–15 |
| All-nine freeze, no LLM pilots, held-out blindness, delta-only roadmap | Global constraints, 10, 11, 15 |
| v1 compatibility, fingerprint coverage, repeatable reports | 1–8, 11, 15 |
