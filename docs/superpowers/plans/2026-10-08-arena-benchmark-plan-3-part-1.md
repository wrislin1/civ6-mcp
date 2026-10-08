# Arena benchmark Plan 3 Part 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the authoring toolkit, signed scoring contract, and three script-validated development positions before any library model trials.

**Architecture:** Keep `SingleTurnAgent`, `BenchmarkRunner`, registry dispatch, and the atomic evidence store as the execution path. Add a scripted backend at `RunnerDependencies.make_agent`, separate version-2 evidence/scoring/report modules, and a recipe-driven authoring CLI that reuses production deployment and reload verification. Complete software validation and the historical classifier regression offline before starting any live authoring clock.

**Tech Stack:** Existing Python 3.12+, uv, asyncio, pytest/pytest-asyncio, PyYAML, FireTuner Lua, and the Windows launcher bridge; no new service or Python dependency.

**Spec:** [Approved Part 1 design](../specs/2026-10-08-arena-benchmark-plan-3-part-1-design.md), including the written-review amendments dated 2026-10-08. The [parent design](../specs/2026-08-30-arena-controlled-position-benchmark-design.md) governs later screen and advancement rules.

**Infrastructure:** [SystemOne server and consumer handoff](../../handoffs/2026-10-08-systemone-decision-servers.md). Decision-model integration is designed separately.

## Global Constraints

- Evidence, predicate, and report schema versions are `2.0.0`. Preserve version-1 interpretation and the frozen Plan 2 admission restrictions; reproduce exact old reports with their pinned revision and fingerprint.
- **No Plan 3 primary score or primary-score improvement is comparable to a Plan 2 primary score or improvement.** Observation earns zero; partial credit is an observable endpoint, never movement toward a target.
- `primary_score = (gross_credit - harm_total) / M`, without clipping or shifting. Each position has three four-point objectives, `M = 12`, optional two-point intermediates, and a finite penalty maximum no greater than 12.
- Default harm weight equals the associated objective's maximum, four points here. Declare exceptions and compensation before model exposure. Deduplicate losses; no survival-credit objective or separate missed-objective debit.
- `max_steps = 15` counts backend round trips, including a finish-only response. Report non-finish tool-call attempts per round trip and dispatched calls. Never infer rounds from tool rows.
- Each version-2 capture uses one Lua execution and has a **2.0-second hard wall limit**, including measured drains, query, parse, normalisation, and digest. Capture timeout and confirmed episode-deadline interruption are infrastructure failures; external task cancellation propagates unchanged.
- Scripted validation uses `actor_kind: scripted`, `counting: false`, `max_steps: 15`, and `episode_wall_s: 300`. Model endpoint, model seed, token usage, cost, and model latency are not applicable. The scripted wall is an operational limit, not model admission evidence.
- Later model blocks use `max(300, ceil(15 * p95_roundtrip_s * 1.5))`. Common context, completion-token, and result-character limits are frozen before model exposure.
- Freeze the exact 35-tool list below as `plan3-part1-v1`; append `finish_trial` only as agent control. No `end_turn`, mutable tier alias, briefing/playbook implementation, tracker arm, or decision-model arm in Part 1.
- SystemOne endpoints are decision actors, outside the five-model chat screen and the `chat(...) -> Reply` backend contract used by `SingleTurnAgent`. Their availability does not authorize library inference or add decision-server admission to the Part 1 exit gate.
- No tested-model episode on a library position in Part 1. All nine rubrics freeze together before viewing tested-model transcripts. Any future development pilot requires an explicit amendment and persistent `pilot-informed` provenance; held-out positions have no exception.
- Three elapsed live hours per scenario attempt, including survey, reload waits, retries, interruptions, verification, and scripted cases. One declared material scenario substitution per family permits a fresh clock; retain both attempts. A second failure blocks that family.
- Reuse existing before/after capture points and twelve-cycle verification. No extra capture loop, historical-mode classifier, hidden script repair, fabricated historical measurements, or new primary utility score.
- Complete the 117-mutation regression offline and the positive-control capture timing probe before live library authoring. Tile-yield and loss ledgers report from day one and never affect advancement.
- Live work follows the [arena operating playbook](../../../tools/skills/civ6-arena-live/SKILL.md): one FireTuner owner, matching Windows companion code, verified reloads, and restored final state.

---

## Review Focus

- Preserve external cancellation; only the agent's expired episode context classifies an interrupted capture as infrastructure failure.
- Audit the literal wire grammar and independent fixture; prove one Lua execution per complete capture and measure both drains inside the 2.0s bound.
- Require the positive-control timing probe before budget freeze, with no library authoring clock or model exposure.
- Pin all 96 tracked historical inputs and both unchanged v1 evaluator hashes; verify the general classifier independently of expected membership.
- Keep endpoint/harm semantics, actor separation and immutable input identities intact across the smaller tasks.
- Require both complete `uv run pytest` gates and tracked, hashed Part 1 evidence sufficient to reproduce reports from a clone.

## File and responsibility map

Paths below are relative to the repository root. Existing modules retain their existing responsibilities; add narrow version dispatch or injected dependencies rather than copying their execution loops.

| Files | Responsibility |
|---|---|
| `benchmarks/toolsets/plan3-part1-v1.yaml`; `arena/benchmark_contract_v2.py`, `arena/benchmark_manifest_v2.py` under `src/civ_mcp/` | Explicit tools and strict version-2 position/script/case/lock validation; dependency fingerprints. |
| `src/civ_mcp/lua/benchmark_v2.py`; `src/civ_mcp/arena/benchmark_state_v2.py` | Complete wire query/parser, canonical evidence, coverage and entity identity. |
| `src/civ_mcp/arena/benchmark_capture.py`, `benchmark_capture_probe.py`; existing `src/civ_mcp/connection.py` | One-execution capture, measured drain phases, cancellation attribution, and a non-library timing probe. |
| `src/civ_mcp/arena/benchmark_lifecycle.py`, `benchmark_predicates_v2.py`, `benchmark_scoring_v2.py` | Evidence-based lifecycle classification, shared cover geometry, endpoint predicates, event losses, compensation, signed score. |
| `src/civ_mcp/arena/benchmark_ledger.py`, `benchmark_audit.py` | Raw measured deltas, lifecycle/loss coverage, one general mutation classifier. |
| `src/civ_mcp/arena/benchmark_scripted.py`, `benchmark_scripted_runner.py`; existing `benchmark_agent.py`, `benchmark_runner.py`, `benchmark_schedule.py` | Finite response batches, counters, scripted trial identity, injected capture, existing execution/persistence. |
| `src/civ_mcp/arena/benchmark_report_v2.py`, `benchmark_validation.py` | Derived reports, locked validation schedule, expected-versus-actual comparison, separate model aggregation admission. |
| `src/civ_mcp/arena/benchmark_authoring_journal.py`, `benchmark_authoring.py` | Persistent scenario clocks, replayable authoring stages, final packet assembly. |
| Existing `benchmark_position.py`, `benchmark_deploy.py`, `game_launcher.py`, `launcher_cli.py` | Inject version-2 capture into existing verification; export immutable archives through the Windows bridge. |
| `tests/arena/benchmark_v2_fixtures.py`, `tests/arena/fixtures/` and task-specific tests below | Small explicit state factories, wire fixtures, historical expected membership, runner integration. |
| `benchmarks/recipes/`, `scripts/`, `validation/`, `positions/`, `provenance/`, `saves/`, `contracts/` | Versioned recipes and final position packets; concrete live bindings are authoring outputs. |
| `docs/research/arena-benchmark-plan-3-part-1-exit.md` | Gate evidence, authoring costs including failures, budget justification, and remaining roadmap deltas. |

Version-2 modules use `dict[str, Any]` for JSON records, with strict validators at their boundaries. They do not expose unvalidated arbitrary dictionaries to evaluation. Unit references are `(owner, id)`, with the engine's full stable ID in evidence and a separately recorded arena `unit_index` for dispatch. Do not confuse `id % 65536` with a globally unique entity key.

Keep immutable authoring inputs separate from the final evidence packet. Freeze `benchmarks/provenance/<position-id>-authoring.json` before verification and scripted validation; the position and run lock hash that file. The later `benchmarks/provenance/<position-id>.json` packet references it, the position, validation runs, and the completed clock journal. Neither the position nor the run lock hashes this later packet, avoiding a circular digest or a provenance file that changes after admission. Failed and in-progress stages remain in the separate append-only authoring journal.

## Evidence retention and clone reproducibility

The `benchmark_runs/` ignore rule does not mean its existing evidence is untracked. The historical 96 trials are already in Git; read those files in place and pin their hashes. For new Part 1 evidence, explicitly force-add the files below for **every** successful or failed scenario/probe attempt under `benchmark_runs/plan3-part1/`. The toolkit produces an `evidence-index.json` per attempt with repository-relative paths and SHA-256 values; it excludes its own digest to avoid recursion. The final packet references the completed index and its digest.

| Tracked paths within an attempt | Contents |
|---|---|
| `authoring-journal.json`, `stages/*.json` | Persistent clock, stage starts/ends/failures, base/setup/export/deploy/capture/twelve-cycle/menu/restore evidence and resolved recipe versions. |
| `observations/*.json`, `mutations/*.json`, `probes/*.json` | Complete public tool results, setup requests/readback and legality/compensation probes, including failures. |
| `samples/*.json`, `summary.json` | Positive-control capture samples, measured drains, identities, scope/row counts and timing verdict. |
| `validation/<position-version>/<suite-id>/session.json`, `trials/*.json`, `attempts/*.json`, `journal.jsonl` beneath that suite | Immutable run lock/schedule, all raw scripted trials, every failed infrastructure attempt and store journal. |
| `report.json`, `report.md`, `validation.json` beneath each validation suite | Deterministic actual reports and expected-case comparison results. |
| `stages/menu-check-*.png`, explicitly referenced supporting JSON/text files | Menu recovery screenshots or other evidence actually used by admission. |
| `evidence-index.json` | Complete finite path/hash inventory, including referenced files outside the attempt directory. |

Also track `benchmark_runs/plan3-part1/preflight/pytest.txt` and `exit/pytest.txt`, their result metadata and indices. Archives, recipes, scripts, expected cases, contracts and provenance under `benchmarks/` are added normally. Validate reference closure: every input/report/provenance dependency is present, hash-correct, and Git-tracked; neither a report nor a failure record may rely on an ignored local-only file. Exclude transient process locks/caches/temp files. Force-add only the generated finite inventory, not the entire ignored directory:

```bash
uv run python -m civ_mcp.arena.benchmark_authoring evidence-files --root benchmark_runs/plan3-part1 --output /tmp/plan3-evidence-paths.txt
git add -f --pathspec-from-file=/tmp/plan3-evidence-paths.txt
```

Task 15 implements `evidence-files`: union the recorded indices plus the index paths, check hashes/reference closure, reject paths outside the approved repository evidence scope, and emit one safe relative path per line. Before each evidence commit, `git ls-files --error-unmatch -- PATH` must succeed for every listed file after staging. Task 22 verifies report regeneration from a temporary checkout of the staged candidate tree so ignored local files cannot conceal missing dependencies.


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
| `position` | `schema_version`, `position_id`, `version`, `family`, `split`, archive path/hash and game save name, player ID, expected state/hash, coverage, toolset path/identity, contract identity, rubric, provenance path/hash, frozen public observation reference/hash and `public_task_tiles`, `pilot_informed`. Only development positions are admitted by this Part 1 CLI. |
| `script` | `schema_version`, `script_id`, `batches`; each batch has `calls`, each call has `name` and object `arguments`. Last batch contains `finish_trial`; no later batch exists. No expectations or rubric keys. |
| `case` | `schema_version`, `case_id`, position/script references and digests, `tags`, `expected` containing `score`, `endpoints`, and `ledger` assertion structures defined in Task 13. |
| `validation_suite` | Schema and suite IDs, ordered case references/digests, position/toolset/contract identities, `max_steps: 15`, `episode_wall_s: 300`, explicit result-character cap, `actor_kind: scripted`, `counting: false`. |
| `lock` | Resolved immutable suite, script/case bytes or digests, archive/state/provenance identities, ordered schedule, code/schema identities, operational limits, actor identity; model/seed/token/cost/latency fields are null. |

Implement only these version-2 inputs; do not widen the existing model campaign loader. Fingerprint an explicit, sorted dependency list covering the new modules and the reused predicate/action attribution, agent, runner, store, registry/narration, `GameState`/Lua, reload/deployment, and position helpers. Reject a missing dependency file. Expand that list in the task introducing each new module; test that editing a scorer, classifier, query, tool schema, or dispatch dependency changes the fingerprint. Never claim that the new fingerprint equals the released v1 fingerprint.

- [ ] **Run the new tests plus `tests/arena/test_benchmark_contract.py` and `tests/arena/test_benchmark_manifest.py`.** Expect all pass, including existing rejection of unsupported Plan 2 arms/options.
- [ ] **Commit:** `git add benchmarks/toolsets/plan3-part1-v1.yaml src/civ_mcp/arena/benchmark_contract_v2.py src/civ_mcp/arena/benchmark_manifest_v2.py tests/arena/test_benchmark_contract_v2.py tests/arena/test_benchmark_manifest_v2.py`; `git commit -m "feat(benchmark): freeze Part 1 toolset and v2 input contracts"`.

## Task 2: Specify and implement the single-execution wire query and parser

**Files:**

- Create: `src/civ_mcp/lua/benchmark_v2.py`, `src/civ_mcp/arena/benchmark_state_v2.py`.
- Test: `tests/arena/test_benchmark_state_v2.py`.
- Read: existing `lua/benchmark.py`, `lua/cities.py`, `lua/units.py`, `benchmark_state.py`.

**Interfaces:**

- Produces `build_benchmark_state_query_v2(player_id: int, coverage: dict[str, Any]) -> str` in `lua.benchmark_v2`.
- Produces `parse_state_v2(raw: str, *, coverage: dict[str, Any]) -> dict[str, Any]`, `normalize_state_v2(state: dict[str, Any]) -> dict[str, Any]`, `digest_state_v2(state: dict[str, Any]) -> str` in `benchmark_state_v2`.
- Consumes Task 1's canonical hashing. The async capture adapter and shared fixtures are Task 3.

**Wire grammar:** one UTF-8 line per record, fields separated by `|`, exactly the field order below. `i` is a decimal integer excluding booleans; `n` is a finite decimal number; `b` is `0` or `1`; `s` is UTF-8 text with `%`, `|`, CR and LF escaped as `%25`, `%7C`, `%0D`, `%0A` (decode once, reject unknown escapes). `~` means unavailable/not applicable only in fields marked `?`; game absence uses an explicit value such as `NONE`, not an omitted row. A literal `~` in a string is escaped as `%7E`. Numeric precision is round-trip decimal (`%.17g`), with integral values canonicalized after parsing.

```text
BEGIN|2.0.0|coverage_sha256:s
IDENTITY|civ_type:s|seed:i|turn:i|active_player:i|player_id:i|gold:n|faith:n
UNIT|owner:i|id:i|unit_index:i|unit_type:s|role:s|x:i|y:i|hp:n|max_hp:n|moves:n|charges:i
TARGET|owner:i|id:i|tracked:b|role:s?|hostile:b|visible:b|status:s|x:i?|y:i?|hp:n?|max_hp:n?
CITY|owner:i|id:i|name:s|x:i|y:i|population:i|housing:n
BUILDING|owner:i|city_id:i|building_type:s|present:b|pillaged:b
DISTRICT|owner:i|city_id:i|district_id:i|district_type:s|x:i|y:i|complete:b|pillaged:b
QUEUE|owner:i|city_id:i|item_kind:s|item_type:s|repair:b|target_x:i?|target_y:i?
TILE|x:i|y:i|owner:i|terrain:s|feature:s|resource:s|improvement:s|pillaged:b|district:s|visible:b|food:n|production:n|gold:n|science:n|culture:n|faith:n
RESOURCE|resource_type:s|access:b|stock:n?|flow:n?
END|2.0.0|identity_count:i|unit_count:i|target_count:i|city_count:i|building_count:i|district_count:i|queue_count:i|tile_count:i|resource_count:i
```

The existing transport `SENTINEL` is printed **after** `END` and consumed by `GameConnection`; it is not a substitute for the count-bearing end record. There is exactly one BEGIN, one IDENTITY and one END. Families occur in the listed order; order within a family is immaterial. Emit one QUEUE per owned city (`NONE|NONE|0|~|~` for empty), all owned units/cities/buildings/districts, all owned tiles plus the frozen discoverable area, every resource type's access status, every frozen tracked target, and visible nearby hostile units needed by cover geometry. Deduplicate tracked/nearby targets by owner/ID. Complete building enumeration makes omitted building types absent; counts alone do not authorize dropping a failed row. Unknown row tags, duplicate keys, dangling city references, count mismatches, missing required rows, and output after END fail capture.

Target status is `alive_visible`, `alive_not_visible`, or `destroyed`. Visible targets require role, coordinates and health. Hidden targets cannot earn destruction credit. Verified destroyed targets have visibility 0 and null position/health; their tracked identity must still have a row. Unavailable optional resource quantities have explicit null coverage; required scoring facts cannot be null. Tile yields always have six values in the stated order.

Canonical root identity keys match v1, with `units`, `targets`, `cities`, `tiles`, `resources` and scope/completeness metadata. Map UNIT `unit_type` to `type`; group BUILDING/DISTRICT/QUEUE records under their owning city, including an explicit empty queue. TILE yields become `yields` with the six named keys. Every semantic set is sorted by owner/ID or coordinate before hashing. Missing essential metadata is an error, not a default field supplied by the fixture helper.

- [ ] **Write independent grammar tests before the query/parser.** Use literal, hand-written wire strings; never produce parser input using the query builder or a serializer sharing the parser's field definitions. Test escaped names, row arity/counts, duplicates, missing identity/end, nonfinite values and target visibility semantics.

```python
import pytest
from civ_mcp.arena.benchmark_state import BenchmarkStateError
from civ_mcp.arena.benchmark_state_v2 import parse_state_v2
from civ_mcp.arena.benchmark_contract_v2 import document_digest

def test_truncated_capture_is_not_an_empty_world():
    coverage = {"include_owned_tiles": True, "area": [], "tracked_targets": []}
    raw = (f"BEGIN|2.0.0|{document_digest(coverage)}\n"
           "IDENTITY|CIVILIZATION_KOREA|7|100|0|0|100|0\n")
    with pytest.raises(BenchmarkStateError, match="incomplete"):
        parse_state_v2(raw, coverage=coverage)
```

The positive complete fixture is authored from the grammar in Task 3; this negative test does not stand in for that independent oracle. Add positive literal row tests here for each field family and a valid computed coverage digest.

- [ ] **Run `uv run pytest tests/arena/test_benchmark_state_v2.py -q`; expect missing imports, then grammar failures before implementation.**
- [ ] **Implement one Lua program that emits all families.** A v2 capture executes **one** `execute_read`/GameCore Lua command, with no per-city, per-unit, per-tile or supplemental InGame query. Reuse `GetMovesRemaining()`, `GetMaxDamage() - GetDamage()`, `GetGrowth():GetHousing()`, plot `GetYield(0..5)`, and the existing GameCore production/repair readback patterns. `GetCurrentProductionTypeHash()` is the InGame alternative; the capture uses the verified `CurrentlyBuilding()` GameCore path. If a required fact cannot be read in that execution, block the candidate capture contract instead of silently adding another query.

```python
def require_complete(actual: dict[str, int], declared: dict[str, int]) -> None:
    from civ_mcp.arena.benchmark_state import BenchmarkStateError
    if actual != declared or declared.get("identity") != 1:
        raise BenchmarkStateError("incomplete v2 capture: row counts disagree")
```

Define `require_complete` in `benchmark_state_v2`. Include `document_digest(coverage)` in BEGIN, validate it against the caller's scope, and build counters from successfully emitted records. A Lua read error emits an error and cannot emit a successful END. Validate before normalising; sort all entity and nested lists, canonicalise numbers, and hash with Task 1's bytes. Keep timing outside state. Preserve v1 parsing and avoid editing `action_metrics.py` or `benchmark_report.py`, whose historical digests are pinned in Task 9.

- [ ] **Run new parser tests and `tests/arena/test_benchmark_state.py`.** Verify semantic ordering does not change hashes and applying the old `state_digest` wrapper to normalized v2 state is idempotent.
- [ ] **Commit the query/parser/tests and fingerprint update:** `git commit -m "feat(benchmark): define complete single-query v2 wire contract"`.

## Task 3: Add the capture adapter, independent fixtures, and transport timing

**Files:**

- Modify: `src/civ_mcp/arena/benchmark_state_v2.py`, `src/civ_mcp/connection.py`.
- Create: `tests/arena/benchmark_v2_fixtures.py`, `tests/arena/fixtures/benchmark_state_v2.txt`.
- Test: `tests/arena/test_benchmark_state_v2.py`, `tests/test_connection_timing.py`.

**Interfaces:**

- Produces `capture_state_v2(conn: Any, player_id: int, coverage: dict[str, Any], *, io_timing: dict[str, Any] | None = None) -> Awaitable[dict[str, Any]]`.
- Extends `GameConnection.execute_read(lua_code: str, timeout: float = 5.0, *, timing: dict[str, Any] | None = None, retry_on_disconnect: bool = True) -> Awaitable[list[str]]`. Existing defaults and write behavior stay compatible. Thread optional read timing/retry policy through `_execute_and_collect` and `_locked_execute`.
- Produces fixture helper `state_v2(*, units=(), cities=(), tiles=(), targets=(), gold=100, faith=0) -> dict[str, Any]`, emitting explicit mandatory root/coverage fields, turn100/player0 and no invented per-entity health/hostility.

- [ ] **Hand-write the fixture from Task 2's grammar.** Include an owned builder, a visible tracked enemy, one city/building/district/queue, two tiles and one resource row. Pin literal expected values for each parsed family in a separately written test. Substitute only the coverage digest token with its independently computed hash; the query builder must never generate this fixture. Derive the helper's default root shape from those explicit expected fields, not by round-tripping the parser.

```python
from pathlib import Path
from civ_mcp.arena.benchmark_contract_v2 import document_digest
from civ_mcp.arena.benchmark_state_v2 import parse_state_v2

def test_literal_wire_field_order():
    coverage = {"include_owned_tiles": True, "area": [[10, 10], [11, 10]],
                "tracked_targets": [[1, 70001]]}
    raw = Path("tests/arena/fixtures/benchmark_state_v2.txt").read_text()
    raw = raw.replace("COVERAGE_SHA256", document_digest(coverage))
    state = parse_state_v2(raw, coverage=coverage)
    assert state["units"][0]["charges"] == 1
    assert state["units"][0]["hp"] == 100
    assert state["cities"][0]["housing"] == 5
    assert state["tiles"][0]["yields"]["food"] == 2
```

The fixture's UNIT ends `|100|100|2|1`, CITY has population4/housing5, and tile `(10,10)` has yields `2|1|0|0|0|0`. Its END is `END|2.0.0|1|1|1|1|1|1|1|2|1`. Use those literal facts in independent assertions. Add an adapter fake recording its calls; require exactly one Lua execution and zero readback fan-out.

- [ ] **Run `uv run pytest tests/arena/test_benchmark_state_v2.py tests/test_connection_timing.py -q`; confirm adapter/timing failures.**
- [ ] **Implement the one-call adapter and measure actual connection phases.**

```python
async def capture_state_v2(conn, player_id, coverage, *, io_timing=None):
    import asyncio
    from civ_mcp.arena.benchmark_state import BenchmarkStateError
    from civ_mcp.lua.benchmark_v2 import build_benchmark_state_query_v2
    query = build_benchmark_state_query_v2(player_id, coverage)
    try:
        lines = await conn.execute_read(query, timeout=2.0, timing=io_timing,
                                        retry_on_disconnect=False)
    except TimeoutError:
        raise  # The bounded wrapper classifies this deadline.
    except (OSError, asyncio.IncompleteReadError) as exc:
        raise BenchmarkStateError("v2 capture transport failed") from exc
    return normalize_state_v2(parse_state_v2("\n".join(lines), coverage=coverage))
```

Record monotonic `pre_drain_s`, `post_drain_s`, `response_wait_s`, `lock_wait_s`, `connect_s`, and `lua_executions` in the supplied dictionary. Time the existing `drain_messages(..., timeout=0.1)` and `timeout=0.2` awaits; do not replace measurements with a hard-coded 0.3 subtraction. Use `finally` around each timed phase so partial observations survive exceptions. Missing phases are unavailable, not measured zero. Increment `lua_executions` at the actual command-send point. With retries disabled, a disconnect becomes the typed `BenchmarkStateError` handled by the existing infrastructure recovery path; it cannot dispatch a second capture query. Cancellation remains uncaught. Timing remains opt-in, does not change drains, and never enters the state digest.

- [ ] **Run adapter, connection timing, existing state and launcher tests affected by the read signature.** Test no hidden retry, drain accounting with a controlled clock, cancellation propagation through drains, and identical parsed state with/without timing. Require one execution for every successful capture.
- [ ] **Commit the adapter, fixtures, connection tests and fingerprint update:** `git commit -m "feat(benchmark): capture once and expose transport drain timing"`.

## Task 4: Bound capture cost without swallowing cancellation

**Files:**

- Create: `src/civ_mcp/arena/benchmark_capture.py`.
- Modify: `src/civ_mcp/arena/benchmark_agent.py`, `src/civ_mcp/arena/benchmark_runner.py`, `src/civ_mcp/arena/benchmark_position.py`.
- Test: `tests/arena/test_benchmark_capture.py`, existing agent/runner/position tests.

**Interfaces:**

- Produces `CaptureFailure(BenchmarkStateError)` and `CaptureTelemetry` with `records: list[dict[str, Any]]`, `current_io: dict[str, Any]`, `cancelled_capture: dict[str, Any] | None`, `reset() -> None`, `summary(*, episode_wall_s: float) -> dict[str, Any]`.
- Produces `capture_bounded(read: Callable[[], Awaitable[dict[str, Any]]], *, phase: str, telemetry: CaptureTelemetry, limit_s: float = 2.0) -> Awaitable[tuple[dict[str, Any], str]]`.
- Adds optional telemetry to `SingleTurnAgent`/`RunnerDependencies`, and optional `capture_state` injection to `capture_position`/`verify_position` with the existing `(conn, player_id, relevant_tiles)` callable shape. The v2 closure supplies coverage and `telemetry.current_io` to Task 3's adapter.

- [ ] **Write separate external-cancel, capture-deadline and episode-deadline tests.** An external cancellation must emerge as `CancelledError` through the wrapper, real agent and runner, without an infrastructure retry or committed trial. An agent deadline during a capture must become `CaptureFailure`; its deadline during a backend call retains `EpisodeTimedOut`. A local two-second capture timeout is independently an infrastructure failure. Test CPU overrun and telemetry reset too.

```python
import asyncio
import pytest
from civ_mcp.arena.benchmark_capture import CaptureTelemetry, capture_bounded

@pytest.mark.asyncio
async def test_caller_cancel_is_not_capture_failure():
    entered = asyncio.Event()
    async def blocked_read():
        entered.set()
        await asyncio.Event().wait()
    telemetry = CaptureTelemetry()
    task = asyncio.create_task(capture_bounded(
        blocked_read, phase="tool_before", telemetry=telemetry))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert telemetry.cancelled_capture is not None
    assert telemetry.records[-1]["complete"] is False
```

- [ ] **Run `uv run pytest tests/arena/test_benchmark_capture.py tests/arena/test_benchmark_agent.py tests/arena/test_benchmark_runner.py -q`; confirm the new tests fail before code.**
- [ ] **Implement the capture wrapper with cancellation telemetry in `finally`, never a `CancelledError` conversion.**

```python
import asyncio
import time
from civ_mcp.arena.benchmark_state_v2 import digest_state_v2

async def capture_bounded(read, *, phase, telemetry, limit_s=2.0):
    started = time.monotonic()
    complete = False
    telemetry.current_io = {}
    try:
        async with asyncio.timeout(limit_s):
            state = await read()
            digest = digest_state_v2(state)
        if time.monotonic() - started > limit_s:
            raise CaptureFailure("capture exceeded wall limit")
        complete = True
        return state, digest
    except TimeoutError as exc:
        # A local capture/transport timeout is not external cancellation.
        raise CaptureFailure("capture or transport exceeded its deadline") from exc
    finally:
        task = asyncio.current_task()
        cancelled = not complete and task is not None and task.cancelling() > 0
        record = {"phase": phase, "duration_s": time.monotonic() - started,
                  "complete": complete, "cancelled": cancelled,
                  "io": dict(telemetry.current_io)}
        telemetry.records.append(record)
        if cancelled:
            telemetry.cancelled_capture = record
```

The wrapper's local timeout converts its own cancellation into `TimeoutError`; external cancellation passes through unchanged. The wrapper records which capture was active during unwinding. **Only the agent knows whether the enclosing cancellation was its episode deadline.** Amend the existing `run` timeout handler at that point:

```python
# Replace the existing try/except inside SingleTurnAgent.run:
try:
    async with asyncio.timeout(self.episode_wall_s) as cm:
        return await self._run_episode(gs, player_id, turn)
except TimeoutError as exc:
    if not cm.expired():
        raise
    if (self._capture_telemetry is not None
            and self._capture_telemetry.cancelled_capture is not None):
        raise CaptureFailure("episode deadline interrupted capture") from exc
    raise EpisodeTimedOut(
        f"benchmark episode exceeded episode_wall_s={self.episode_wall_s}",
        partial_evidence=self.partial_evidence(),
    ) from exc
```

This is the existing timed-run block with the added capture check, not a new outer cancellation catch. Leave `CancelledError` uncaught in the agent and runner. Reset the latch before each episode/attempt; never use a cancellation from an earlier attempt. Root initial/final capture cancellation also propagates. Local capture failures and confirmed episode-during-capture failures use existing infrastructure recovery; shutdown cancellation never invokes that retry path. Do not use `uncancel()`.

Summary includes count/mean/nearest-rank p95/max/total, actual pre/post drain totals, and non-drain residual (`duration - measured drains`, labelled as including query/transport/parse/digest). In-episode share includes only tool-before/after phases; initial/final captures are outside episode wall. Report unavailable phase data honestly. Two captures plus one read-tool Lua call naturally allocate about two thirds of step time to capture, much of it drain; this ratio alone is not a query-performance defect. The hard 2.0s bound includes drains; all successful records require one Lua execution. Task 17 tests feasibility before budget freeze or any library clock.

- [ ] **Run wrapper/agent/runner/position tests.** Inspect failed-attempt files for real capture failures and absence of retry/score commits on external cancellation. Preserve exactly twelve verified reload cycles and ordinary backend timeout classification.
- [ ] **Commit the Task 4 files and fingerprint update:** `git commit -m "feat(benchmark): bound captures while preserving caller cancellation"`.


## Task 5: Implement endpoint predicates and one civilian-safety geometry

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
- [ ] **Commit the Task 5 files and fingerprint update:** `git commit -m "feat(benchmark): add endpoint and civilian exposure predicates"`.

## Task 6: Derive signed scores, event deductions, and progress attribution

**Files:**

- Create: `src/civ_mcp/arena/benchmark_scoring_v2.py`.
- Test: `tests/arena/test_benchmark_scoring_v2.py`.
- Modify: `src/civ_mcp/arena/benchmark_manifest_v2.py` to call rubric validation.

**Interfaces:**

- Produces `validate_rubric(rubric: dict[str, Any], initial: dict[str, Any]) -> None`, `score_trial(trial: dict[str, Any], rubric: dict[str, Any]) -> dict[str, Any]`, `attribute_progress(trial: dict[str, Any], rubric: dict[str, Any]) -> list[dict[str, Any]]`.
- Objective record: `id`, `rungs: [{points, predicate}]`. Harm record: `id`, `loss_key`, `objective_id`, `weight`, `weight_reason`, `timing` (`final` or `event`), `predicate`, `compensation: [{timing, predicate}]`, and explicit integer `priority`. Compensation timing is `event` or `final`; every evaluated event predicate receives its raw transition. Each loss key binds a finite named asset/event scope.
- Score result: per-objective credit/evidence, `harms` (fired/compensated/deduplicated records with evidence), `gross_credit`, `harm_total`, `net_credit`, `maximum_credit`, `maximum_harm`, `primary_score`, and increment derivation.

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
- [ ] **Commit the Task 6 files and fingerprint update:** `git commit -m "feat(benchmark): score progress and deduplicated harm separately"`.

## Task 7: Derive the measured benefit ledger

**Files:**

- Create: `src/civ_mcp/arena/benchmark_ledger.py`.
- Test: `tests/arena/test_benchmark_ledger.py`.

**Interfaces:**

- Consumes Task 5's `classify_lifecycle(step)` and complete canonical snapshots.
- Produces `measured_delta(before: float | None, after: float | None) -> dict[str, Any]`, `build_ledger(trial: dict[str, Any]) -> dict[str, Any]`.
- Ledger shape: `net` and `steps`; each measured entry has `path` (stable string components), `before`, `after`, `delta`, `units`, `coverage`, entity/tile references and source step indices. `path` is unique within net or a particular step.

- [ ] **Write outcome tests, including improve/undo, city-total-only drift, and unavailable measurements.**

```python
from civ_mcp.arena.benchmark_ledger import measured_delta

def test_unavailable_is_not_zero_benefit():
    assert measured_delta(None, 2) == {
        "before": None, "after": 2, "delta": None, "coverage": "unavailable"}
    assert measured_delta(3, 3)["delta"] == 0
```

- [ ] **Run `uv run pytest tests/arena/test_benchmark_ledger.py -q`; confirm failure.**
- [ ] **Implement measured vectors, without utility weights.**

```python
def measured_delta(before, after):
    if before is None or after is None:
        return {"before": before, "after": after, "delta": None,
                "coverage": "unavailable"}
    return {"before": before, "after": after, "delta": after - before,
            "coverage": "measured"}
```

Compare initial/final and every step's before/after by stable entity or tile key. Emit six tile yields only for changed tiles, measured resource access, gold/faith, and charges. Keep observable one-off harvest receipts distinct. Use the lifecycle classifier to recognize final-charge consumption; never assume any disappearing unit spent one charge. Net initial/final deltas cannot be a sum of only positive steps. Example paths are `["gold"]`, `["tiles", "10,10", "food"]`, and `["units", "0:1", "charges"]`. A field present only after the action has unavailable before coverage. The ledger never affects primary scoring or compensation.

- [ ] **Run ledger tests with real v2 fixture states.** A gain-then-undo trial has zero net gain and two separate step entries; citizen reassignment changing city totals emits no tile yield gain. Legitimate last charge records consumption without destruction.
- [ ] **Commit the ledger/tests and fingerprint update:** `git commit -m "feat(benchmark): derive measured benefit ledger"`.

## Task 8: Classify uncredited mutations and audit all losses

**Files:**

- Create: `src/civ_mcp/arena/benchmark_audit.py`.
- Test: `tests/arena/test_benchmark_audit.py`.

**Interfaces:**

- Consumes `classify_lifecycle`, `build_ledger`, and objective-progress attribution from the appropriate contract scorer.
- Produces `mutation_records(trial: dict[str, Any], progress: list[dict[str, Any]], *, task_tiles: list[tuple[int, int]]) -> list[dict[str, Any]]`, `classify_mutation(record: dict[str, Any]) -> dict[str, Any]`, `audit_losses(trial: dict[str, Any], declared_losses: list[dict[str, Any]]) -> list[dict[str, Any]]`.
- Classification has one `category`, secondary `tags`, raw evidence references, and no primary deduction of its own. Categories: `declared_harm`, `undeclared_loss`, `economic_change`, `builder_positioning`, `non_builder_movement`, `insufficient_evidence`.

- [ ] **Write tests for unscored asset loss on credited, uncredited and error-returning actions.**

```python
from civ_mcp.arena.benchmark_audit import classify_mutation

def test_deleted_unscored_asset_has_a_report_bucket():
    record = {"step": 3, "declared_harm_ids": [], "objective_ids": [],
              "losses": [{"kind": "unit", "entity": [0, 9],
                          "lifecycle": "lost", "declared": False}],
              "economic_changes": [], "movement": None,
              "coverage": {"loss": "complete"}}
    result = classify_mutation(record)
    assert result["category"] == "undeclared_loss"
    assert result["primary_deduction"] == 0
```

- [ ] **Run `uv run pytest tests/arena/test_benchmark_audit.py -q`; confirm failure.**
- [ ] **Implement one classifier, with category precedence and loss cross-links.**

```python
def mutation_category(record):
    if record["declared_harm_ids"]:
        return "declared_harm"
    if any(loss["lifecycle"] == "lost" and not loss["declared"]
           for loss in record["losses"]):
        return "undeclared_loss"
    if record["economic_changes"]:
        return "economic_change"
    movement = record["movement"]
    if movement is not None:
        return "builder_positioning" if movement["builder"] else "non_builder_movement"
    return "insufficient_evidence"
```

Define `mutation_category` in `benchmark_audit`; keep secondary effects/tags visible even when precedence selects another category. `mutation_records` selects successful state-changing calls lacking objective-progress attribution. `audit_losses` separately examines **every** complete transition, including credited and error-returning actions. Link its results into uncredited records where applicable. Unknown lifecycle remains unresolved; a verified transformation is not destruction. Add the distinct under-credit audit for completed outcomes that received less than completion credit.

General descriptive tags include `farm_on_own_tile`, `closer_to_public_task`, `same_distance_to_public_task`, `farther_from_public_task`, and `non_builder_move`. Public task coordinates come from archived tool observations, never the rubric's target list. Distance remains descriptive and never grants primary credit. Version-1 missing yields/health have explicit unavailable coverage; they do not require a second classifier or fabricated values.

- [ ] **Run classifier, lifecycle and ledger tests.** Verify all-action loss coverage, compensated/undeclared distinctions, non-builder moves and same/farther task distances. Preserve all raw entity/tool references.
- [ ] **Commit the classifier/tests and fingerprint update:** `git commit -m "feat(benchmark): classify mutations and expose undeclared losses"`.

## Task 9: Pin the historical audit to tracked inputs and unchanged v1 evaluators

**Files:**

- Create: `tests/arena/fixtures/builder_uncredited_audit_v1.json`, `tests/arena/test_benchmark_historical_audit.py`.
- Modify: `src/civ_mcp/arena/benchmark_audit.py` to add only the fixture-driven regression entry point.
- Read unchanged: `src/civ_mcp/arena/benchmark_report.py`, `src/civ_mcp/arena/action_metrics.py`, the [historical audit](../../research/arena-benchmark-builder-calibration-uncredited-actions-audit.md), tracked campaign trials/manifests and public task-list evidence.

**Interfaces:** Produces `reproduce_audit(fixture_path: Path, *, root: Path) -> dict[str, Any]`, using Task 8's same classifier after v1 attribution. Inputs are tracked files with exact SHA-256 identities; there is no copied/compressed corpus or historical-mode classifier.

All 96 trial files under `benchmark_runs/builder-economy-cal-v{1,2}/blocks/*/trials/*.json` are already tracked despite the directory's ignore rule. Both evaluator files are byte-identical to `bf0f0b5`. Pin these reviewed SHA-256 values in the fixture and regression test:

| File | SHA-256 at `bf0f0b5` and at review |
|---|---|
| `src/civ_mcp/arena/benchmark_report.py` | `1a417f60a89effb9d58a1620dde4f875e226da6f3e33c2406d2f8903cee1ec5c` |
| `src/civ_mcp/arena/action_metrics.py` | `652923662e2a87f1437e93d2abc132996eadc5859bbab4c558f7cfd4e2c07709` |

- [ ] **Write digest guards and expected-membership tests.**

```python
import hashlib
from pathlib import Path
from civ_mcp.arena.benchmark_audit import reproduce_audit

def test_historical_evaluators_match_reviewed_commit():
    expected = {
        "src/civ_mcp/arena/benchmark_report.py": "1a417f60a89effb9d58a1620dde4f875e226da6f3e33c2406d2f8903cee1ec5c",
        "src/civ_mcp/arena/action_metrics.py": "652923662e2a87f1437e93d2abc132996eadc5859bbab4c558f7cfd4e2c07709",
    }
    for path, digest in expected.items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest

def test_all_raw_trials_reproduce_historical_membership():
    result = reproduce_audit(Path("tests/arena/fixtures/builder_uncredited_audit_v1.json"), root=Path("."))
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

- [ ] **Run `uv run pytest tests/arena/test_benchmark_historical_audit.py -q`.** The digest guard already passes; the new missing fixture/entry point must fail.
- [ ] **Create the expected fixture from tracked evidence and implement independent enumeration.** Fixture schema contains `evaluator_revision`, `evaluator_files`, `inputs` (all 96 raw path/hash entries plus rubric and public task-list source path/hash entries), `expected_membership` (`campaign`, `block`, `trial`, `step`, `tag`), and two separate `expected_undercredits`. Require every input path to be tracked at fixture construction; runtime fails on missing/altered files. Verify the set of raw paths equals all trials in both campaigns before scoring. Never choose input actions from expected membership.

```python
def checked_bytes(root, item):
    import hashlib
    data = (root / item["path"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != item["sha256"]:
        raise ValueError(f"historical input changed: {item['path']}")
    return data
```

Define `checked_bytes` in `benchmark_audit`. Recompute v1 objective-progress attribution from every raw step using the unchanged files above, then normalize and classify with Task 8. Compare actual membership only after computing it. Pin public `get_builder_tasks` text from a tracked trial/step, including its text digest; preserve the audit's 45 split 36/9 and both quarries. Name/digest the later city-yield supplement separately; old raw tile yields remain unavailable. Do not update historical hashes to make changed code pass; keep v2 behavior in new modules.

- [ ] **Run historical, classifier and v1 action/report tests.** Temporary fixture copies with an altered expectation must mismatch; a missing/altered tracked-input copy must fail before scoring. Confirm a fresh checkout needs no generated bundle or untracked raw source.
- [ ] **Commit the fixture, entry point, tests and fingerprint update:** `git commit -m "test(benchmark): reproduce historical audit from tracked evidence"`.


## Task 10: Add a finite scripted backend and direct round-trip counters

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

Define `batch_reply` in `benchmark_scripted`. Internal `Reply` zero-token defaults are transport compatibility only; scripted persisted model-token fields become null in Task 11. Increment `round_trips` immediately before each backend call and completed rounds after a reply, including finish-only/implicit-finish replies; expose unfinished rounds on timeout. Increment attempts for every non-finish emitted call, including rejected names/malformed arguments, and dispatch count only when invoking the registry. Preserve processing of every game call in a finish-containing batch. Lock `max_steps=15` at validation admission, leaving generic agent defaults and Plan 2's eight-step manifest unchanged.

- [ ] **Run scripted and existing agent tests.** Add real `SingleTurnAgent` tests with two calls plus finish in one round, finish in a separate second round, malformed arguments, and step cap. Require `attempts / round_trips` of 2.0 versus 1.0 for equivalent calls in those two batching layouts. No token double-counting across a batch.
- [ ] **Commit the Task 10 files and fingerprint update:** `git commit -m "feat(benchmark): run scripted replies through the benchmark agent"`.

## Task 11: Inject scripted trials into the production runner

**Files:**

- Create: `src/civ_mcp/arena/benchmark_scripted_runner.py`.
- Modify: `src/civ_mcp/arena/benchmark_runner.py`, `src/civ_mcp/arena/benchmark_schedule.py`.
- Test: `tests/arena/test_benchmark_scripted_runner.py`; existing runner/store tests.

**Interfaces:**

- Produces `ScriptedTrialSpec(index: int, position_id: str, arm_id: str, script_id: str, case_id: str)` with `model=None`, `seed=None`, `pair_id=None`; callbacks accept `TrialSpec | ScriptedTrialSpec` without changing the legacy schedule.
- Produces `make_scripted_agent(spec: ScriptedTrialSpec, *, script: dict[str, Any], toolset: dict[str, Any], capture_state: Callable, telemetry: CaptureTelemetry, tile_coords: tuple[tuple[int, int], ...], wall_s: float, char_cap: int) -> SingleTurnAgent` and `run_scripted_suite(suite: dict[str, Any], run_dir: Path) -> Awaitable[list[dict[str, Any]]]` in `benchmark_scripted_runner`.

- [ ] **Write a runner integration fixture that dispatches a real registered tool.** Inject the fake world only at `GameState` methods/connection transport. Do not replace registry dispatch or the agent with a score-producing fake. A real `fortify_unit` or `move_unit` call must alter that world and appear between captured snapshots in the atomic trial file. Test lock-change refusal, failed-attempt resume, no duplicate commit, and external cancellation propagation.

```python
from civ_mcp.arena.benchmark_schedule import ScriptedTrialSpec

def test_script_identity_does_not_impersonate_a_model():
    spec = ScriptedTrialSpec(index=1, position_id="test", arm_id="validation",
                             script_id="observe", case_id="null")
    assert spec.model is None
    assert spec.seed is None
    assert spec.pair_id is None
```

- [ ] **Run `uv run pytest tests/arena/test_benchmark_scripted_runner.py -q`; confirm failure.**
- [ ] **Implement the factory and preserve runner/store authority.**

```python
def make_scripted_agent(spec, *, script, toolset, capture_state, telemetry,
                        tile_coords, wall_s, char_cap):
    from civ_mcp.arena.benchmark_agent import SingleTurnAgent
    from civ_mcp.arena.benchmark_scripted import ScriptedBackend
    backend = ScriptedBackend(script, game_tools=tuple(toolset["game_tools"]))
    return SingleTurnAgent(backend, tuple(toolset["game_tools"]),
                           episode_wall_s=wall_s, max_steps=15, char_cap=char_cap,
                           tile_coords=tile_coords, capture_state=capture_state,
                           capture_telemetry=telemetry, evidence_version="2.0.0")
```

Use `RunnerDependencies.make_agent` for a fresh backend each attempt. Reuse deployment, confirmed reload, popup hygiene, initial/final captures, attempt limits and immutable store commits. Persist v2 actor/script/case/tool/contract/scope identities, round/call counters and timing, with model/seed/token/cost/latency fields null. Bind script/case bytes, ordered schedule and all immutable dependencies in the lock. Invalid required operations, unconsumed scripts, implicit finish, missing captures, and turn/player drift fail validation with raw evidence retained. A deliberately rejected operation must be declared as such in its validation case; no hidden repair or dropped call.

Scripts have no model admission or model health canary. An execution timeout fails validation with game-health evidence, never a model `runaway_timeout`. Preserve capture failures as infrastructure attempts and caller cancellation as cancellation. `run_scripted_suite` returns committed raw records; scoring and expected-case comparison occur in later tasks, not in the factory.

- [ ] **Run new integration, runner, store, agent and position tests.** Inspect actual persisted trials and attempts. Verify atomicity/provenance, no backend probe, no caller-cancel retry, and no expected score in the actor's input.
- [ ] **Commit the runner factory/spec/tests and fingerprint update:** `git commit -m "feat(benchmark): inject scripted trials into production runner"`.

## Task 12: Generate deterministic version-2 reports

**Files:**

- Create: `src/civ_mcp/arena/benchmark_report_v2.py`.
- Test: `tests/arena/test_benchmark_report_v2.py`.
- Leave unchanged: `src/civ_mcp/arena/benchmark_report.py` and `action_metrics.py` (Task 9's digest pins).

**Interfaces:** Produces `build_trial_report(trial: dict[str, Any], position: dict[str, Any]) -> dict[str, Any]`, `render_report(report: dict[str, Any]) -> str`, `validate_model_inputs(trials: list[dict[str, Any]]) -> None`. Consumes Tasks 6–9 scoring/ledger/audit and Task 11's persisted raw evidence. Report contains `score`, `ledger`, `audits`, `capture`, `counters`, and immutable provenance.

- [ ] **Write report determinism and actor-separation tests.**

```python
import pytest
from civ_mcp.arena.benchmark_report_v2 import validate_model_inputs

def test_scripted_trials_cannot_enter_model_comparisons():
    with pytest.raises(ValueError, match="scripted"):
        validate_model_inputs([{"schema_version": "2.0.0", "actor_kind": "scripted",
                                "counting": False}])
```

- [ ] **Run `uv run pytest tests/arena/test_benchmark_report_v2.py -q`; confirm failure.**
- [ ] **Compose derived results without changing v1 routing.**

```python
def build_trial_report(trial, position):
    from civ_mcp.arena.benchmark_scoring_v2 import score_trial, attribute_progress
    from civ_mcp.arena.benchmark_ledger import build_ledger
    from civ_mcp.arena.benchmark_audit import audit_losses, mutation_records, classify_mutation
    score = score_trial(trial, position["rubric"])
    progress = attribute_progress(trial, position["rubric"])
    records = mutation_records(trial, progress, task_tiles=position["public_task_tiles"])
    return {"score": score, "ledger": build_ledger(trial), "audits": {
        "uncredited": [classify_mutation(record) for record in records],
        "losses": audit_losses(trial, score["harms"]),
    }}
```

Complete this composition with the existing under-credit audit, immutable provenance, identity/turn checks, terminal/truncation, counters and capture summaries. `public_task_tiles` is derived from the frozen public observation record, not objective tiles; bind it and its source digest in the v2 position input. No expected-case data enters reports. Report each objective, fired/compensated/deduplicated harm, gross/harm/net/denominator/signed score, measured net/step ledger, and all-action loss coverage.

Include per-episode non-finish attempts/rounds and dispatched counts; eligible future model aggregation shows n/min/median/p95/max per model. Mixed contract/tool identities and mixed versions/scopes within a position fail before aggregation; different positions may have their own scopes. Show total and in-episode capture cost, measured pre/post drain totals and non-drain residual separately. Scripted model latency/token/cost stay null. Stable ordering and input timestamps only; two renders of the same locked evidence must be byte-identical.

- [ ] **Run report, historical digest and existing v1 report tests.** Verify altered expectations cannot change a derived report; source timing never changes canonical digests; all referenced evidence survives a local clone.
- [ ] **Commit the report/tests and fingerprint update:** `git commit -m "feat(benchmark): render signed v2 evidence reports"`.

## Task 13: Compare validation cases and expose the validation CLI

**Files:**

- Create: `src/civ_mcp/arena/benchmark_validation.py`.
- Test: `tests/arena/test_benchmark_validation.py`.
- Modify: strict `case` validation in `benchmark_manifest_v2.py` for the assertion shapes below.

**Interfaces:**

- Produces `run_validation(suite_path: Path, run_dir: Path) -> Awaitable[dict[str, Any]]`, `check_case(report: dict[str, Any], expected: dict[str, Any], *, trial: dict[str, Any]) -> dict[str, Any]`.
- CLI: `uv run python -m civ_mcp.arena.benchmark_validation run --suite PATH --run-dir PATH`, and `report --run-dir PATH --output PATH`.
- `expected` has exactly `score` (expected score fields), `endpoints` (`id`, endpoint `predicate`, Boolean `value`), and `ledger` (`id`, `scope`, `step`, `path`, `coverage`, `delta`) assertions. Scope is `net` with step null, or `step` with a real step index. IDs are unique across endpoint/ledger assertions. No expressions or arbitrary comparator callbacks.

- [ ] **Write actual endpoint/ledger cases, including missing/unavailable measurement.**

```python
from civ_mcp.arena.benchmark_validation import check_case

def test_expectation_cannot_override_score_or_missing_ledger():
    report = {"score": {"primary_score": -1 / 3}, "ledger": {"net": [], "steps": []}}
    expected = {"score": {"primary_score": 1.0}, "endpoints": [], "ledger": [{
        "id": "gold", "scope": "net", "step": None, "path": ["gold"],
        "coverage": "measured", "delta": 0}]}
    result = check_case(report, expected, trial={})
    assert not result["passed"]
    assert set(result["mismatches"]) == {"score:primary_score", "gold"}
    assert report["score"]["primary_score"] == -1 / 3
```

- [ ] **Run `uv run pytest tests/arena/test_benchmark_validation.py -q`; confirm failure.**
- [ ] **Implement the comparison explicitly after deriving the report.**

```python
def check_case(report, expected, *, trial):
    from civ_mcp.arena.benchmark_predicates_v2 import evaluate_predicate
    mismatches = {}
    for key, value in expected["score"].items():
        actual = report["score"].get(key)
        if actual != value:
            mismatches[f"score:{key}"] = {"actual": actual, "expected": value}
    for assertion in expected["endpoints"]:
        actual = evaluate_predicate(assertion["predicate"],
                                    initial=trial["initial_state"], final=trial["final_state"])
        if actual != assertion["value"]:
            mismatches[assertion["id"]] = {"actual": actual, "expected": assertion["value"]}
    for assertion in expected["ledger"]:
        rows = report["ledger"]["net"]
        if assertion["scope"] == "step":
            rows = [row for step in report["ledger"]["steps"]
                    if step["step"] == assertion["step"] for row in step["entries"]]
        matches = [row for row in rows if row["path"] == assertion["path"]]
        target = {key: assertion[key] for key in ("coverage", "delta")}
        actual = ({key: matches[0][key] for key in target} if len(matches) == 1 else None)
        if actual != target:
            mismatches[assertion["id"]] = {"actual": actual, "expected": target}
    return {"passed": not mismatches, "mismatches": mismatches}
```

Strictly validate all assertions before this function: permitted score fields/types, finite values, valid endpoint predicates, scope/step consistency, Boolean endpoint expectations, measured versus unavailable deltas and unique paths/IDs. Missing required predicate evidence raises rather than becoming false; missing/duplicate ledger matches fail even if expected delta is zero. Event-only predicates are excluded from endpoint assertions; expected harm IDs/details live under `score`. Compare exact canonical numbers; do not use a loose score tolerance. A test with a real `civilian_covered` predicate must fail/pass as its fixture endpoint changes, and a measured delta assertion must fail when coverage is unavailable.

`run_validation` loads/checks locks, invokes Task 11's suite runner, builds Task 12 reports, then calls `check_case`. Keep expected cases out of the scripted factory/backend. The report-only CLI reads retained raw evidence and refuses changed dependencies. Nonpassing cases retain actual reports and fail the validation command; no expected value overrides derived output. Give initial/final state and ledger paths explicit source references in mismatches.

- [ ] **Run validation, scripted-runner, report and manifest tests.** Verify real dispatch integration end-to-end, rejected required operations, byte-identical reports, wrong identity/turn failure, and changed script/case lock refusal.
- [ ] **Commit the CLI/assertion tests and fingerprint update:** `git commit -m "feat(benchmark): validate endpoint and ledger assertions"`.


## Task 14: Persist authoring clocks and export immutable native saves

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
- [ ] **Commit the Task 14 files and fingerprint update:** `git commit -m "feat(benchmark): retain authoring attempts and export immutable saves"`.

## Task 15: Build the replayable authoring stage machine

**Files:**

- Create: `src/civ_mcp/arena/benchmark_authoring.py`. Concrete recipes are Task 16.
- Test: `tests/arena/test_benchmark_authoring.py`.
- Modify: `benchmark_manifest_v2.py` for recipe/authoring assertion validation and `benchmark_position.py` only for the injected v2 capture already defined.

**Interfaces:**

- Produces `load_recipe(path: Path) -> dict[str, Any]`, `run_authoring_stage(recipe_path: Path, *, stage: str, attempt_dir: Path) -> Awaitable[dict[str, Any]]`.
- CLI: `uv run python -m civ_mcp.arena.benchmark_authoring STAGE --recipe PATH --attempt-dir PATH`, where `STAGE` is `survey`, `apply`, `probe`, `archive`, `capture`, `verify`, `menu-check`, `validate`, `finish`. Every live stage requires the persistent attempt journal and an unexpired clock.
- Offline CLI: `preflight --recipes PATH... --output PATH`, `gate --packets PATH... --output PATH`, and `evidence-files --root PATH --output PATH`. These never connect to the game. Produce the finite evidence inventory/closure checks described above; each completed or failed stage records immutable paths and hashes.

- [ ] **Write a fake-transport stage-order test.** Assert that survey starts the clock before connecting, apply always reloads the identified base, probes dispatch through the frozen tools, archive cannot precede successful readback, verification is exactly twelve cycles, menu-check uses recovery loading, and finish requires every gate plus final restored digest. Failed/expired stages retain evidence and never issue later mutations.

```python
import pytest
from civ_mcp.arena.benchmark_authoring import validate_stage_transition

def test_archive_requires_legality_probe_evidence():
    with pytest.raises(ValueError, match="probe"):
        validate_stage_transition("archive", {"survey": "passed", "apply": "passed"})
```

- [ ] **Run `uv run pytest tests/arena/test_benchmark_authoring.py -q`; confirm failure.**
- [ ] **Implement orchestration around existing operations and validate the recipe schema.** Define `validate_stage_transition(stage: str, completed: dict[str, str]) -> None` in `benchmark_authoring`, with the ordered stages above; survey/apply/probe may repeat under the same clock with every result retained. Repeating an earlier stage invalidates downstream certificates; changed bindings require a new immutable archive/position version and complete revalidation under the same scenario clock. Recipe fields: schema/recipe/scenario/family/version, base identity/hash, player, setup operations and assertions, survey queries, binding rules, coverage rule, proposed objectives/harms, toolset, probes, archive name/path, packet paths, script/case definitions and expected results. Include `predecessor`, `substitution_reason`, and `material_change` (null for attempt1, all required for a substitute); pass these to `AuthoringJournal.begin` before its first live command. Validate raw authoring Lua separately from script tool calls; it cannot appear in an episode.

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

1. Survey only through the proposed public tools, saving raw results for candidate discoverability. Tie each required candidate fact to the actual result text delivered under the locked character cap, so untruncated private evidence cannot stand in for what an actor could observe. Also obtain the private complete evidence needed for setup verification, keeping it out of the observation record. The SystemOne consumer will need bounded menus, but implementing those menus or imposing server-specific candidate limits on this library is outside Part 1.
2. Apply from the hashed base every time. Record exact Lua setup requests/results and resolved entities. Use existing verified setup patterns (`UnitManager.PlaceUnit`, `UnitManager.RestoreMovement`, `ImprovementBuilder.SetImprovementPillaged`) where applicable; prove any additional setup operation by readback. No raw mutation is allowed after the scenario archive is frozen for scripts.
3. Probe every required endpoint/harm/compensation through registry tools on disposable setup copies, recording actual results and restoring the declared start between probes. Do not call speculative remote `CanStartOperation` checks that bypass the established unit-action path. Avoid the unresolved camp-on-ivory case.
4. Reapply the recipe from base after probes, assert the intended initial state, call existing `game_lifecycle.save_game`, export a new immutable archive, then call `capture_position` with the v2 adapter. A source-save acknowledgment alone is insufficient evidence of a complete archive.
5. Freeze the immutable `-authoring.json` input, then materialize the strict position, scripts and separate cases, run `verify_position(..., cycles=12)`, and test the existing crash-recovery menu load against that exact archive. Persist hashes and reload confirmation, not just a claimed menu success. Final packet/journal timing data is separate from the already hashed authoring input.
6. Run the locked validation suite through Task 13, regenerate reports twice, restore the scenario archive, verify its digest, and finish the authoring clock. Admission checks the whole evidence packet; a family cannot pass on separate incompatible versions of its witnesses.

The three concrete family recipes are a separate offline deliverable in Task 16. Stage outputs follow the evidence-retention layout, with no overwrite of failed attempts. `finish` completes the clock/journal before hashing its final index; the later packet references that index, and the index does not reference the packet back.

- [ ] **Run the authoring tests and position/deploy tests.** Use a fake native bridge and fake world; assert archived expected scores cannot be read by the backend, failed probes prevent archive admission, and replay starts from base rather than the previous mutated state. A required public fact present only in a tool result's capped-away suffix must fail discoverability until an allowed observation actually delivers it. Missing/altered inventory entries or untracked referenced evidence must fail the offline closure gate; temporary locks/caches must not enter the force-add list.
- [ ] **Commit the Task 15 files and fingerprint update:** `git commit -m "feat(benchmark): add replayable position authoring workflow"`.

## Task 16: Commit the three offline authoring recipes

**Files:**

- Create: `benchmarks/recipes/plan3-builder-a1.yaml`, `benchmarks/recipes/plan3-city-a1.yaml`, `benchmarks/recipes/plan3-tactical-a1.yaml`.
- Test: `tests/arena/test_benchmark_part1_recipes.py`.
- Read: spec section9, the Task 15 recipe schema/stage API, and `benchmarks/provenance/builder-posctrl-v1-authoring.json`.

**Interfaces:** Consumes `load_recipe(path)` and the stage machine; produces three validated recipes with declared binding rules, setup/probe operations, objective/harm shapes, public discovery queries, script/case templates, and immutable output paths. Actual live IDs/coordinates are recorded outputs of survey/probes, not missing design choices or invented constants.

- [ ] **Write an offline recipe contract test before creating the recipes.**

```python
from pathlib import Path
from civ_mcp.arena.benchmark_authoring import load_recipe

def test_three_recipes_share_surface_and_finite_score_contract():
    maxima = {"builder": 8, "city": 4, "tactical": 4}
    for family, harm_max in maxima.items():
        recipe = load_recipe(Path(f"benchmarks/recipes/plan3-{family}-a1.yaml"))
        assert recipe["toolset_id"] == "plan3-part1-v1"
        assert recipe["positive_maximum"] == 12
        assert recipe["harm_maximum"] == harm_max
        assert recipe["max_steps"] == 15
        assert recipe["predecessor"] is None
```

- [ ] **Run `uv run pytest tests/arena/test_benchmark_part1_recipes.py -q`; expect missing recipe files.**
- [ ] **Write the recipes using the verified organic base and common identity fields.** Each starts with this block, using its own `family`/`scenario_id` and harm maximum:

```yaml
schema_version: 2.0.0
family: builder
scenario_id: builder-a1
predecessor: null
substitution_reason: null
material_change: null
toolset_id: plan3-part1-v1
max_steps: 15
positive_maximum: 12
harm_maximum: 8
base_save_identity:
  name: SEONDEOK 100 400 BC
  sha256: 2cd485b005cb2afe2d58ceaac60be56dd80ea3d5ccc66f6796427d9057c2ab29
```

Add the Task 15 schema's declared setup operations, binding selectors, assertions and evidence paths. Selectors must specify unique resolution criteria and fail with the candidate list if ambiguous; they do not mutate the base until the live task's clock starts. Bindings are resolved and committed before archive freeze, with every revision journaled.

| Recipe | Required scenario and endpoint contract | Required loss/compensation probe |
|---|---|---|
| Builder | Builders start away from repair/resource/food sites; navigation, scarce sufficient charges, competing work and visible route threat; 2-point legal occupied endpoint or 4-point verified completion for each of three objectives. | Escort loss4 and new exposure4; legitimate attack/compensation, covered route, temporary exposure repaired, final-charge completion. No camp-on-ivory dependency. |
| City | Separate/compatible city queues; housing remedy queued2 or completed with resolved shortfall4; suitable committed district4; urgent active repair4. Enough treasury and legal alternatives for joint maximum. | Irreversible unacceptable asset displacement4; accepted productive replacement does not fire; queue overwrite removes commitment credit but not past loss. |
| Tactical | Already-hostile visible attacker; meaningful measured damage2 or verified neutralization4; civilian reaches covered endpoint4; reinforcement queued2 or in defended area4. | Uncompensated military loss4 versus accepted tactical compensation; ordinary damage is not harm; initially exposed null stays zero. |

Each recipe declares a public observation-only discoverability script, joint-full and materially different alternative-full trajectories, every intermediate rung, applicable harm positive/negative cases, mixed/harm-only and repeat/undo cases. Actual bindings and tactical damage threshold must be frozen from measured readback before validation, never model behavior. Restrict scripts to registry tools; raw setup Lua belongs only to authoring. All positive rungs are false initially. Store script templates and expected-case templates separately so the backend never sees expected scores.

- [ ] **Run recipe and authoring-stage tests.** Confirm all three share frozen 35 tools, three × four-point objectives, finite harm scope and immutable output naming. Reject missing discoverability source references, ambiguous binding rules and mutually incompatible proposed objectives. Joint feasibility itself is proved by the later live witnesses, not asserted as an offline pass.
- [ ] **Commit the three recipes and tests:** `git commit -m "feat(benchmark): declare three Part 1 authoring recipes"`.

## Task 17: Probe v2 capture timing on the positive control before budget freeze

**Files:**

- Create: `src/civ_mcp/arena/benchmark_capture_probe.py`, `tests/arena/test_benchmark_capture_probe.py`.
- Produce during execution: `benchmarks/provenance/plan3-part1-capture-probe.json` and `benchmark_runs/plan3-part1/capture-probe/` raw evidence.
- Read unchanged: `benchmarks/positions/builder-posctrl-v1.yaml` and its archive/provenance.

**Interfaces:** Produces `probe_capture(position_path: Path, *, samples: int, output_dir: Path) -> Awaitable[dict[str, Any]]`; CLI `uv run python -m civ_mcp.arena.benchmark_capture_probe --position PATH --samples 20 --output-dir PATH`. Only the identified positive-control archive is admitted; no model backend is constructed. Consume bounded v2 capture plus measured connection phases from Tasks 3–4, existing deployment/reload and v1 identity checks.

- [ ] **Write offline probe-gate tests.** Twenty complete samples with identical digest, unchanged turn/player, one Lua execution each and all durations ≤2.0s pass. Missing drain measurements, drift, a second query or an overrun fail. Pin sample count20 and positive-control identity; reject a library position.

```python
from civ_mcp.arena.benchmark_capture_probe import timing_probe_passes

def test_two_queries_cannot_pass_by_being_fast():
    row = {"complete": True, "duration_s": 0.9, "lua_executions": 2,
           "pre_drain_s": 0.1, "post_drain_s": 0.2, "digest": "same"}
    assert not timing_probe_passes([dict(row) for _ in range(20)])
```

- [ ] **Run `uv run pytest tests/arena/test_benchmark_capture_probe.py -q`; confirm failure.**
- [ ] **Implement the probe and its summary gate, using production capture/reload operations.**

```python
def timing_probe_passes(rows):
    return (len(rows) == 20 and len({row["digest"] for row in rows}) == 1
            and all(row["complete"] and row["duration_s"] <= 2.0
                    and row["lua_executions"] == 1
                    and row["pre_drain_s"] is not None
                    and row["post_drain_s"] is not None for row in rows))
```

Define `timing_probe_passes` in the probe module; additionally validate finite nonnegative durations and exact turn/player identity before constructing rows. Production flow: deploy/confirmed reload positive control; compare its v1 identity/digest; freeze a representative v2 scope containing **all owned tiles**, the public discoverable area around owned cities/units, and visible tracked hostiles; record the exact scope and row counts; execute20 bounded v2 observations; verify final unchanged v1/v2 identity; disconnect. Use a generous public area covering the three recipe families' anticipated reachable actions, record area/entity counts, and retain the per-position full-scope null gates for later admission. The probe cannot prove performance for a larger future scope.

No action/setup mutation, library archive, or model inference occurs. Survey queries that construct this probe's scope are recorded outside timed captures. Each timed capture is exactly one Lua execution with query/parse/digest and both measured drains inside the 2.0s limit. Report count/mean/p95/max/total, pre/post drain totals, non-drain residual and payload/row counts; do not call the residual pure engine compute time. A failure blocks budget freeze and library authoring until query optimization or a prospective budget amendment is validated here.

- [ ] **Run probe and capture/connection tests before touching the game.** Then, during implementation execution, use the arena operating playbook to acquire single FireTuner ownership and run this one-off live probe:

```bash
uv run python -m civ_mcp.arena.benchmark_capture_probe --position benchmarks/positions/builder-posctrl-v1.yaml --samples 20 --output-dir benchmark_runs/plan3-part1/capture-probe
```

This is a preflight on the existing **non-library** positive control, so it consumes no three-hour library authoring attempt. Retain failed probe samples/reasons and a new attempt identity for any justified rerun. Do not tune away required snapshot fields. Record a capture implementation digest over the query, parser, capture wrapper, connection/tuner transport, numeric normalization and canonical-hash dependencies. That digest and the probe scope/sample hashes must be referenced by Task 18; later gate-only code changes do not pretend to be a new capture timing measurement. Any capture-dependency change requires a fresh positive-control probe before freeze.

- [ ] **Commit the probe/tests/report and track probe evidence under the evidence-retention rule:** `git commit -m "feat(benchmark): validate capture budget on positive control"`. Task 18 cannot proceed with only an offline fake timing result.


## Task 18: Freeze the candidate contract after timing and full-suite preflight

**Files:**

- Create: `benchmarks/contracts/instrument-v2.yaml`, `benchmarks/contracts/instrument-v2.md`, `benchmarks/contracts/plan3-part1-budget.yaml`, `benchmarks/provenance/plan3-part1-offline-preflight.json`.
- Test: `tests/arena/test_benchmark_part1_gate.py`.
- Modify: `benchmark_authoring.py` for the offline gate only; do not connect to FireTuner in this task.

**Interfaces:**

- Produces `check_part1_packet(packet: dict[str, Any]) -> dict[str, Any]` and `check_part1_gate(packets: list[dict[str, Any]], preflight: dict[str, Any]) -> dict[str, Any]` in `benchmark_authoring`.
- Preflight binds code/scorer/toolset identities, full-suite results, the successful Task 17 timing-probe digest/capture implementation identity, historical input/report digests and recipe versions. Gate output names failed requirements and source evidence paths, never a silent Boolean with no reason.

- [ ] **Write table-driven gate tests.** Begin with a complete synthetic packet, remove one required piece at a time (twelve checks, menu verification, alternative full score, harm negative case, null digest, capture timing, final restore, failed-attempt history, offline audit, positive-control timing probe, tracked evidence inventory, no-model provenance); each removal must fail for that reason. A passing substitute includes both scenario durations.

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
capture_lua_executions: 1
positive_control_timing_samples: 20
model_episode_wall_formula: "max(300, ceil(15 * p95_roundtrip_s * 1.5))"
primary_comparable_to_plan2: false
benefit_ledger_gates_advancement: false
authoring_seconds_per_scenario: 10800
scenario_substitutions_per_family: 1
```

In the accompanying contract Markdown cite the parent fifteen-round baseline and Plan 2 v3 evidence (Qwen cap 4/12; batching roughly two calls/round versus Gemma one), preserving the distinction between evidence supporting restoration and proof of an optimal budget. List v2 predicate/report schema, all fingerprint dependencies, finite penalty rules, coverage and toolset identities. Mark this as the candidate contract used for scripted authoring, then release it at Task 22 only if all gates pass. A code/contract amendment during live work is prospective, retains prior evidence, and forces affected packet revalidation under the new identity; it does not reset scenario time.

- [ ] **Run the entire repository suite.** Run `uv run pytest`, retaining its output and actual result under `benchmark_runs/plan3-part1/preflight/pytest.txt`; the benchmark subset does not cover all connection, agent, launcher, CLI and deployment consumers. Run `uv run python -m civ_mcp.arena.benchmark_authoring preflight --recipes benchmarks/recipes/plan3-builder-a1.yaml benchmarks/recipes/plan3-city-a1.yaml benchmarks/recipes/plan3-tactical-a1.yaml --output benchmarks/provenance/plan3-part1-offline-preflight.json`. Expect passing runner/dispatch integration, offline 117 membership/counts, strict no-model scripts, the prior successful live timing probe, and zero game/network calls during this offline preflight. The full suite must pass before freezing the contract; the live probe is required evidence from Task 17, not rerun inside this command. Record actual test results; never copy historical pass counts.
- [ ] **Commit the candidate contract, gate, preflight and tests:** `git commit -m "feat(benchmark): preregister Part 1 budget and acceptance gate"`.

## Task 19: Author and validate the builder-economy position

**Files:**

- Finalize: `benchmarks/recipes/plan3-builder-a1.yaml`.
- Create: `benchmarks/positions/plan3-builder-a1-v1.yaml`, `benchmarks/saves/plan3-builder-a1-v1.Civ6Save`, `benchmarks/provenance/plan3-builder-a1-v1-authoring.json`, `benchmarks/provenance/plan3-builder-a1-v1.json`, `benchmarks/scripts/plan3-builder-a1-v1/`, `benchmarks/validation/plan3-builder-a1-v1/`.
- Evidence: `benchmark_runs/plan3-part1/builder-a1/`, including retained attempts and raw scripts.

**Interfaces:** Consumes the Task 15 stage CLI and Task 18 passing preflight; produces a packet accepted by `check_part1_packet`. The alternate allowed scenario is `builder-a2` with a declared material change and independent immutable versions.

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

## Task 20: Author and validate the city-planning position

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

## Task 21: Author and validate the tactical-defense position

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

## Task 22: Evaluate the exit gate and publish the Part 1 evidence record

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

- [ ] **Verify report reproducibility from tracked raw evidence.** Stage the finite force-add inventory, verify every path/hash with Git, and create a temporary checkout of the staged candidate tree. Generate each report there twice into separate temporary outputs and compare bytes with the retained originals; verify archive, raw evidence, script, expected-case, toolset, scorer and provenance hashes. No live reload is needed for report regeneration. Run the entire `uv run pytest` suite at this exit gate and retain its actual output under `benchmark_runs/plan3-part1/exit/pytest.txt`, as well as the pre-freeze full-suite result. If code changed, invalidate/revalidate every affected live packet before admission; a passing test suite alone cannot restore its old source identity.
- [ ] **Write the exit report with actual evidence and release the contract only on pass.** Include per-objective/harm results, negative/compensated cases, ledger and undeclared-loss coverage, null chain and timing summaries, authoring stage/family costs, classifier membership/counts, and the restored-budget derivation. Distinguish scripted mechanics validation from model decision quality. Release records name precise schema/source/toolset identities, not an unqualified “v2 passed.”
- [ ] **Update only roadmap deltas.** Remaining work: author the other three development and three held-out positions, extend the common tools under a new identity if needed and revalidate all affected positions, freeze all nine together, admit the five-model roster, derive per-position interpretation scales, then use the parent's unchanged screen/advancement/Stage 3/Stage 4 rules. Briefing/playbook are separate injections; tracker remains multi-turn; decision-model work requires a separate same-menu-control design incorporating the separate SystemOne server/consumer handoff. No model pilot is authorized by this task.
- [ ] **Commit the gate/exit/status record:** `git commit -m "docs(benchmark): record Plan 3 Part 1 exit evidence"`. Do not mark this task or Part 1 complete when any gate remains failed.

## Deferred SystemOne consumer

The [server/consumer handoff](../../handoffs/2026-10-08-systemone-decision-servers.md#civ6-mcp-consumer-design-requirements) contains the decision transport, admission, timing, probability and telemetry requirements.
That architecture needs a separate design and the same-menu LLM control.
Part 1 supplies public discoverability and verified position packets; it adds no SystemOne task or library inference.

## Plan review and coverage

This is the implementation handoff; tasks remain unchecked. This review does not claim implementation or live execution.

| Requirement | Tasks |
|---|---|
| Frozen tool/input contracts and v1 compatibility | 1, 2, 9, 12, 18 |
| Literal wire grammar, one-execution capture and independent fixtures | 2, 3 |
| Capture deadline versus external cancellation; measured drain overhead | 3, 4, 17 |
| Endpoint/harm semantics, cover geometry and signed scoring | 5, 6 |
| Tile ledger, all-action loss coverage and historical 117 regression | 7, 8, 9 |
| Scripted actor, real runner dispatch, deterministic reports and explicit assertions | 10, 11, 12, 13 |
| Authoring clock/substitution, native export, stage machine and recipes | 14, 15, 16 |
| Positive-control timing probe before freeze; no library clock | 17, 18 |
| Full repository suite before freeze and at exit | 18, 22 |
| Three jointly feasible, blind development packets and complete live gate | 19, 20, 21, 22 |
| Force-added raw evidence and clone-reproducible reports | Evidence retention, 15, 17–22 |
| Delta roadmap and separate decision-model consumer | 22, deferred consumer pointer |
