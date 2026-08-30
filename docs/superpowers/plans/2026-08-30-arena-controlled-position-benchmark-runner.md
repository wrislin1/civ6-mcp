# Arena Controlled-Position Benchmark Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the reproducible, single-turn benchmark runner and its measurement, reload, deployment, state-verification, evidence, and reporting foundations.

**Architecture:** A dedicated serial runner reuses arena model/tool primitives but bypasses `ArenaCoordinator`. It deploys a versioned Windows save, reloads and verifies queried state before each trial, runs one objective-blind decision episode, commits immutable raw evidence, and derives scores afterward.

**Tech Stack:** Python 3.12, asyncio, dataclasses, PyYAML, OpenAI-compatible chat completions, Civ VI FireTuner Lua, pytest/pytest-asyncio, native Windows launcher bridge.

**Spec:** `docs/superpowers/specs/2026-08-30-arena-controlled-position-benchmark-design.md`

## Global Constraints

- Trials are strictly serial because FireTuner port 4318 is single-client.
- Stage 1 never exposes or calls `end_turn`.
- Every trial receives a fresh conversation context.
- `finish_trial` is present in every benchmark tool tier.
- Raw scoreable evidence is committed before scoring; bad model outcomes are never retried away.
- Maximum infrastructure attempts per scheduled trial is 3.
- A queried-state checksum mismatch aborts the session and produces no model observation.
- WSL and Windows companion checkouts must be clean and at the same commit for recorded runs.
- Conflicting GPU work blocks by default; draining requires an explicit run-scoped acknowledgment naming the services.
- Reports read only the session lock and `trials/`, never `attempts/`.
- The benchmark prompt is objective-blind and contains no position-specific hints or rubric content.
- This plan is Plan 1 of 2. It delivers tested runner software and synthetic fixtures. Exact live saves, frozen development/held-out rubrics, and recorded campaigns require a second position-authoring/campaign plan after the runner can capture real canonical state.

---

## File Structure

### New focused modules

- `src/civ_mcp/arena/action_metrics.py` — classify invalid calls, domain rejections, successful mutations, useful progress, and repetition.
- `src/civ_mcp/arena/popups.py` — shared context-native popup dismissal.
- `src/civ_mcp/arena/benchmark_backend.py` — seed, identity, health, and representative-latency probes.
- `src/civ_mcp/arena/benchmark_manifest.py` — position/suite dataclasses, YAML parsing, validation, and fingerprints.
- `src/civ_mcp/arena/benchmark_schedule.py` — immutable ABBA/model-screen schedule expansion.
- `src/civ_mcp/arena/benchmark_deploy.py` — WSL-to-Windows save deployment and boot-health bridge.
- `src/civ_mcp/arena/benchmark_state.py` — canonical state normalization, hashing, and diffs.
- `src/civ_mcp/arena/benchmark_store.py` — append-only journal, attempts, atomic raw trials, and resume.
- `src/civ_mcp/arena/benchmark_agent.py` — objective-blind one-episode tool loop.
- `src/civ_mcp/arena/benchmark_gates.py` — clean-tree, treatment, endpoint, seed, warm-latency, and state gates.
- `src/civ_mcp/arena/benchmark_runner.py` — serial orchestration and failure admission.
- `src/civ_mcp/arena/benchmark_report.py` — deterministic scoring and report rendering.
- `src/civ_mcp/lua/benchmark.py` — one-query benchmark state capture and parser.

### Existing modules changed

- `src/civ_mcp/arena/analyze.py` — render shared action-quality metrics for existing arena runs.
- `src/civ_mcp/arena/backends.py` — explicit sampling and retry policies.
- `src/civ_mcp/arena/coordinator.py` — import the shared popup helper.
- `src/civ_mcp/game_lifecycle.py` — continue in-game Lua loads through the leader screen.
- `src/civ_mcp/game_launcher.py` — native save deployment and fresh-offset boot-health helpers.
- `src/civ_mcp/launcher_cli.py` — structured `install-save` and `boot-health` commands.
- `src/civ_mcp/lua/__init__.py` — export benchmark query/parser.
- `pyproject.toml` — add `civ-arena-benchmark` and `civ-arena-benchmark-report` entry points.

### Tests

- `tests/arena/test_action_metrics.py`
- `tests/arena/test_benchmark_backend.py`
- `tests/arena/test_benchmark_manifest.py`
- `tests/arena/test_benchmark_schedule.py`
- `tests/arena/test_benchmark_state.py`
- `tests/arena/test_benchmark_store.py`
- `tests/arena/test_benchmark_agent.py`
- `tests/arena/test_benchmark_gates.py`
- `tests/arena/test_benchmark_runner.py`
- `tests/arena/test_benchmark_report.py`
- Existing launcher, lifecycle, popup, backend, coordinator, and analyzer tests.

---

### Task 1: Shared action-quality classifier

**Files:**
- Create: `src/civ_mcp/arena/action_metrics.py`
- Create: `tests/arena/test_action_metrics.py`
- Modify: `src/civ_mcp/arena/analyze.py`
- Modify: `tests/arena/test_analyze.py`

**Interfaces:**
- Consumes: transcript `steps`, `invalid_tool_calls`, per-mutation queried `state_before`/`state_after`, and objective mappings.
- Produces: `evaluate_predicate`, `classify_action_quality(*, steps, invalid_tool_calls, objectives=()) -> dict[str, object]`, and `classify_result(result: str) -> str`.

- [ ] **Step 1: Write failing classifier tests**

```python
from civ_mcp.arena.action_metrics import classify_action_quality


def test_classifier_separates_domain_rejection_from_invalid_call_and_loop():
    state_a = {
        "units": [{"id": 8, "x": 9, "y": 10}],
        "tiles": [{"x": 9, "y": 10, "improvement": None}],
    }
    state_b = {
        "units": [{"id": 8, "x": 9, "y": 10}],
        "tiles": [{"x": 9, "y": 10, "improvement": "IMPROVEMENT_MINE"}],
    }
    steps = [
        {"tool_name": "move_unit", "tool_args": {"unit_index": 7, "x": 4, "y": 5},
         "tool_result_full": "Error: BLOCKED", "state_before": state_a,
         "state_after": state_a, "state_digest_before": "a", "state_digest_after": "a"},
        {"tool_name": "move_unit", "tool_args": {"unit_index": 7, "x": 4, "y": 5},
         "tool_result_full": "Error: BLOCKED", "state_before": state_a,
         "state_after": state_a, "state_digest_before": "a", "state_digest_after": "a"},
        {"tool_name": "improve_tile", "tool_args": {"unit_index": 8,
                                                       "improvement_name": "IMPROVEMENT_MINE"},
         "tool_result_full": "IMPROVING|IMPROVEMENT_MINE|9,10",
         "state_before": state_a, "state_after": state_b,
         "state_digest_before": "a", "state_digest_after": "b"},
    ]
    got = classify_action_quality(
        steps=steps,
        invalid_tool_calls=[{"tool_name": "imaginary", "reason": "unknown_tool"}],
        objectives=[{"task_id": "mine", "unit_index": 8, "target": [9, 10],
                     "tools": ["improve_tile"], "progress_predicate": {
                         "kind": "state_changed_to",
                         "path": ["tiles", 0, "improvement"],
                         "value": "IMPROVEMENT_MINE",
                     }}],
    )
    assert got["invalid_calls"] == 1
    assert got["domain_rejections"] == 2
    assert got["successful_mutations"] == 1
    assert got["useful_actions"] == 1
    assert got["repetitions"] == 1
    assert got["loop_excess"] == 1
```

- [ ] **Step 2: Run the new tests and prove they fail**

Run: `uv run pytest -q tests/arena/test_action_metrics.py`

Expected: collection fails because `civ_mcp.arena.action_metrics` does not exist.

- [ ] **Step 3: Add the minimal classifier and analyzer integration**

```python
# src/civ_mcp/arena/action_metrics.py
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence


def classify_result(result: str) -> str:
    normalized = (result or "").strip().lower()
    if normalized.startswith(("error", "unavailable")) or "|blocked" in normalized:
        return "domain_rejection"
    return "success"


def _call_key(step: Mapping[str, object]) -> str:
    payload = {
        "tool": step.get("tool_name"),
        "args": step.get("tool_args") if isinstance(step.get("tool_args"), dict) else {},
        "result": step.get("tool_result_full"),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def classify_action_quality(
    *,
    steps: Sequence[Mapping[str, object]],
    invalid_tool_calls: Sequence[Mapping[str, object]],
    objectives: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    rejected = successful = useful = repetitions = 0
    seen: dict[str, object] = {}
    for step in steps:
        kind = classify_result(str(step.get("tool_result_full", "")))
        rejected += kind == "domain_rejection"
        changed = step.get("state_digest_before") != step.get("state_digest_after")
        successful += kind == "success" and changed
        if objectives:
            useful += kind == "success" and changed and any(
                step.get("tool_name") in objective.get("tools", ())
                and evaluate_predicate(
                    objective["progress_predicate"],
                    initial_state=step.get("state_before"),
                    final_state=step.get("state_after"),
                    steps=[step],
                )
                for objective in objectives
            )
        call_key = _call_key(step)
        if call_key in seen and seen[call_key] == step.get("state_digest_before"):
            repetitions += 1
        seen[call_key] = step.get("state_digest_after")
    return {
        "invalid_calls": len(invalid_tool_calls),
        "domain_rejections": rejected,
        "successful_mutations": successful,
        "useful_actions": useful if objectives else None,
        "useful_action_coverage": "objective_verified" if objectives else "unavailable",
        "repetitions": repetitions,
        "loop_excess": repetitions,
}
```

Before `classify_action_quality`, implement one fail-closed predicate evaluator shared later by the benchmark scorer. Version 1 supports `always`, `all`, `any`, `successful_tool_call` (tool plus exact argument subset), `final_state_equals` (typed path plus value), `state_changed_to`, and `unit_distance_decreased`. Unknown kinds and missing typed paths raise `PredicateError`; they never become a false/zero score. Add one positive and one counterfactual test per predicate.

Wire `analyze.py` to attach the returned mapping under `action_quality` for each played record and aggregate totals by player without changing existing report keys. Historical arena records without objective mappings report `useful_actions: null` and `useful_action_coverage: unavailable`; do not relabel every successful mutation as useful. Domain rejection and repetition remain available from historical transcripts.

- [ ] **Step 4: Run counterfactual and analyzer tests**

Run: `uv run pytest -q tests/arena/test_action_metrics.py tests/arena/test_analyze.py`

Expected: PASS. Then temporarily change the second repeated call's target in the fixture and verify the repetition assertion fails before restoring it.

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/action_metrics.py src/civ_mcp/arena/analyze.py tests/arena/test_action_metrics.py tests/arena/test_analyze.py
git commit -m "feat(arena): classify action quality and domain rejections"
```

---

### Task 2: Complete in-game Lua reloads

**Files:**
- Modify: `src/civ_mcp/game_lifecycle.py`
- Modify: `tests/test_game_lifecycle_load.py`

**Interfaces:**
- Consumes: `game_launcher.continue_after_lua_load(save_name: str) -> Awaitable[str]`.
- Produces: `load_game_save(conn: GameConnection, save_name: str) -> Awaitable[str]` that returns only after the in-game Lua tier reaches a playable world or reports a warning.

- [ ] **Step 1: Add failing in-game continuation tests**

```python
@pytest.mark.asyncio
async def test_ingame_lua_tier_hands_engaged_load_to_continue_helper(monkeypatch):
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _t: real_sleep(0))
    continued: list[str] = []

    async def fake_continue(name):
        continued.append(name)
        return f"Loaded {name}: world ready, FireTuner port is open."

    from civ_mcp import game_launcher
    monkeypatch.setattr(game_launcher, "continue_after_lua_load", fake_continue)
    conn = LoadConn([["QUERY_SENT"], ["RESULT|FOUND"], ["WIPED"]])

    result = await load_game_save(conn, "0_MCP_0306")

    assert continued == ["0_MCP_0306"]
    assert "world ready" in result
```

Add a second test where the verification call raises `ConnectionError`; it must also invoke the helper exactly once.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `uv run pytest -q tests/test_game_lifecycle_load.py -k 'ingame_lua_tier_hands or connection_drop_after_found'`

Expected: FAIL because the in-game tier returns the old generic success string.

- [ ] **Step 3: Route both engagement proofs through the helper**

Replace both early success returns inside the in-game FOUND verification loop with one engagement flag, then:

```python
if engaged:
    from . import game_launcher

    log.info("In-game Lua load engaged for '%s'; waiting through load screen", save_name)
    return await game_launcher.continue_after_lua_load(save_name)
```

Do not call the helper when all polls return `STILL_SET`; retain the inert-Aspyr fallback.

- [ ] **Step 4: Run lifecycle and launcher regression tests**

Run: `uv run pytest -q tests/test_game_lifecycle_load.py tests/test_game_launcher.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/game_lifecycle.py tests/test_game_lifecycle_load.py
git commit -m "fix(launcher): continue in-game Lua loads to a playable world"
```

---

### Task 3: Extract shared popup hygiene

**Files:**
- Create: `src/civ_mcp/arena/popups.py`
- Modify: `src/civ_mcp/arena/coordinator.py`
- Modify: `tests/arena/test_popup_dismiss.py`
- Modify: `tests/arena/test_coordinator.py`

**Interfaces:**
- Produces: `dismiss_blocking_popups(conn) -> Awaitable[str]`.
- Preserves: context-native `Close()`/`OnClose()` behavior and `POPUPS|...` result contract.

- [ ] **Step 1: Move tests to the public module and verify RED**

Change the test import to:

```python
from civ_mcp.arena.popups import dismiss_blocking_popups
```

Rename calls from `_dismiss_blocking_popups(...)` to `dismiss_blocking_popups(...)` without changing assertions.

- [ ] **Step 2: Run popup tests**

Run: `uv run pytest -q tests/arena/test_popup_dismiss.py`

Expected: collection fails because `arena.popups` does not exist.

- [ ] **Step 3: Move the helper without changing its behavior**

Create `arena/popups.py` containing `_POPUP_CONTEXT_CLOSERS` and the current helper body, renamed publicly. Import `civ_mcp.lua as lq`. In `coordinator.py`:

```python
from civ_mcp.arena.popups import dismiss_blocking_popups
```

Replace all three coordinator calls and update coordinator monkeypatches to target `coordinator_mod.dismiss_blocking_popups`.

- [ ] **Step 4: Run popup and coordinator tests**

Run: `uv run pytest -q tests/arena/test_popup_dismiss.py tests/arena/test_coordinator.py`

Expected: PASS with the NaturalDisaster context-native assertions unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/popups.py src/civ_mcp/arena/coordinator.py tests/arena/test_popup_dismiss.py tests/arena/test_coordinator.py
git commit -m "refactor(arena): share context-native popup dismissal"
```

---

### Task 4: Explicit backend sampling and probes

**Files:**
- Modify: `src/civ_mcp/arena/backends.py`
- Create: `src/civ_mcp/arena/benchmark_backend.py`
- Modify: `tests/arena/test_backends.py`
- Create: `tests/arena/test_benchmark_backend.py`

**Interfaces:**
- Produces: `SamplingConfig`, `RetryPolicy`, `BackendProbe`, `HealthProbe`, `probe_backend(backend, messages, tools, samples=10)`, `probe_health(backend, expected_model, timeout_s)`, and `episode_wall_seconds(max_steps, latencies_s)`.
- Existing arena construction with no new arguments preserves current sampling and three-attempt retry behavior.

- [ ] **Step 1: Add failing backend-contract tests**

```python
def test_benchmark_backend_sends_exact_sampling_and_disables_resampling():
    sampling = SamplingConfig(temperature=0.2, top_p=0.95, seed=41, max_tokens=2048)
    backend, capture = _backend_with_capture(
        sampling=sampling,
        retry_policy=RetryPolicy(max_attempts=1, backoff_s=0.0),
    )
    asyncio.run(backend.chat([{"role": "user", "content": "act"}], []))
    assert capture.kwargs["temperature"] == 0.2
    assert capture.kwargs["top_p"] == 0.95
    assert capture.kwargs["seed"] == 41
    assert capture.kwargs["max_tokens"] == 2048
```

```python
def test_episode_wall_uses_p95_and_five_minute_floor():
    assert episode_wall_seconds(max_steps=15, latencies_s=[20.0] * 9 + [30.0]) == 675
    assert episode_wall_seconds(max_steps=15, latencies_s=[2.0] * 10) == 300
```

- [ ] **Step 2: Run backend tests and verify RED**

Run: `uv run pytest -q tests/arena/test_backends.py tests/arena/test_benchmark_backend.py`

Expected: FAIL because the sampling/probe types do not exist.

- [ ] **Step 3: Add frozen configurations and probe functions**

```python
@dataclass(frozen=True)
class SamplingConfig:
    temperature: float | None = None
    top_p: float | None = None
    seed: int | None = None
    max_tokens: int = MAX_COMPLETION_TOKENS


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = MAX_ATTEMPTS
    backoff_s: float = RETRY_BACKOFF_S
```

Store these on `OpenAICompatBackend`; add non-`None` sampling fields to `kw`; drive the retry loop from `RetryPolicy`. Existing arena callers retain `RetryPolicy(max_attempts=3)`, but every counted benchmark backend is constructed with `RetryPolicy(max_attempts=1)` so a hidden SDK/backend retry cannot resample a model episode. Infrastructure retry remains solely in the runner and is visible in `attempts/`.

In `benchmark_backend.py`, calculate nearest-rank p95 and:

```python
def episode_wall_seconds(*, max_steps: int, latencies_s: list[float]) -> int:
    ordered = sorted(latencies_s)
    p95 = ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]
    return max(300, math.ceil(max_steps * p95 * 1.5))
```

`probe_backend` performs ten exact-sampling, full-schema calls, verifies reported model identity, compares repeated/differing seed outputs, and returns the raw latency list plus `seed_honored`. `probe_health` sends one independently bounded identity canary and returns a structured verdict.

- [ ] **Step 4: Run tests and preserve legacy retry semantics**

Run: `uv run pytest -q tests/arena/test_backends.py tests/arena/test_benchmark_backend.py tests/arena/test_agent.py`

Expected: PASS; existing callers still use three attempts and omit unspecified sampling keys.

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/backends.py src/civ_mcp/arena/benchmark_backend.py tests/arena/test_backends.py tests/arena/test_benchmark_backend.py
git commit -m "feat(arena): make sampling and backend probes explicit"
```

---

### Task 5: Position/suite manifests and immutable schedules

**Files:**
- Create: `src/civ_mcp/arena/benchmark_manifest.py`
- Create: `src/civ_mcp/arena/benchmark_schedule.py`
- Create: `tests/arena/test_benchmark_manifest.py`
- Create: `tests/arena/test_benchmark_schedule.py`

**Interfaces:**
- Produces: `PositionManifest`, `SuiteManifest`, `TreatmentArm`, `TrialSpec`, `load_position_manifest`, `load_suite_manifest`, `fingerprint`, and `compile_schedule`.

- [ ] **Step 1: Add manifest and schedule tests**

```python
def test_calibration_schedule_is_twelve_abba_pairs_with_shared_pair_seed():
    suite = SuiteManifest(
        suite_id="builder-cal-v1",
        driver="single_turn",
        positions=("builder-cal-v1",),
        models=("qwen3.6-27b",),
        arms=(
            TreatmentArm("minimal", "minimal", {}),
            TreatmentArm("standard", "standard", {}),
        ),
        seeds=tuple(range(101, 113)),
        order="abba",
        sampling=SamplingConfig(temperature=0.2, top_p=0.95, seed=None, max_tokens=6144),
        max_steps=15,
        result_char_cap=6000,
        audit_indices=(1, 4, 9, 14, 19, 22),
    )
    trials = compile_schedule(suite)
    assert len(trials) == 24
    assert [t.arm_id for t in trials[:8]] == ["minimal", "standard", "standard", "minimal",
                                                    "minimal", "standard", "standard", "minimal"]
    for left, right in zip(trials[::2], trials[1::2], strict=True):
        assert left.pair_id == right.pair_id
        assert left.seed == right.seed
```

```python
def test_manifest_fingerprint_changes_for_prompt_rubric_sampling_or_tool_arm():
    base = {
        "suite_id": "builder-cal-v1",
        "prompt_digest": "p1",
        "rubric_digest": "r1",
        "sampling": {"temperature": 0.2, "top_p": 0.95},
        "arms": [{"id": "minimal", "tools": "minimal"}],
    }
    digests = {
        fingerprint({**base, "prompt_digest": "p2"}),
        fingerprint({**base, "rubric_digest": "r2"}),
        fingerprint({**base, "sampling": {"temperature": 0.4}}),
        fingerprint({**base, "arms": [{"id": "standard", "tools": "standard"}]}),
    }
    assert len(digests) == 4
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `uv run pytest -q tests/arena/test_benchmark_manifest.py tests/arena/test_benchmark_schedule.py`

Expected: collection fails because both modules are missing.

- [ ] **Step 3: Implement strict dataclasses and canonical fingerprints**

Use `yaml.safe_load`, reject unknown/missing keys, normalize tuples/lists, and hash canonical JSON:

```python
@dataclass(frozen=True)
class PositionManifest:
    position_id: str
    version: int
    archive: str
    archive_sha256: str
    game_save_name: str
    player_id: int
    expected_state: dict[str, object]
    expected_state_sha256: str
    relevant_tiles: tuple[tuple[int, int], ...]
    objectives: tuple[dict[str, object], ...]
    rubric: tuple[dict[str, object], ...]
    split: str


@dataclass(frozen=True)
class TreatmentArm:
    arm_id: str
    tools: str
    options: dict[str, object]


@dataclass(frozen=True)
class SuiteManifest:
    suite_id: str
    driver: str
    positions: tuple[str, ...]
    models: tuple[str, ...]
    arms: tuple[TreatmentArm, ...]
    seeds: tuple[int, ...]
    order: str
    sampling: SamplingConfig
    max_steps: int
    result_char_cap: int
    audit_indices: tuple[int, ...]


def fingerprint(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
```

`TrialSpec` is frozen and contains only schedule inputs: `index`, `pair_id`, `position_id`, `model`, `arm_id`, and `seed`. The session fingerprint is deliberately absent: it is computed only after the schedule and startup evidence are locked, then stamped onto raw artifacts by `BenchmarkStore`. `compile_schedule` expands explicit seed lists and ABBA ordering; it refuses duplicate indices, unbalanced audit indices, a missing `finish_trial`, or any arm exposing `end_turn`.

- [ ] **Step 4: Run tests and a YAML round-trip**

Run: `uv run pytest -q tests/arena/test_benchmark_manifest.py tests/arena/test_benchmark_schedule.py tests/arena/test_config.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/benchmark_manifest.py src/civ_mcp/arena/benchmark_schedule.py tests/arena/test_benchmark_manifest.py tests/arena/test_benchmark_schedule.py
git commit -m "feat(arena): define benchmark manifests and schedules"
```

---

### Task 6: Native Windows save deployment and boot health

**Files:**
- Modify: `src/civ_mcp/game_launcher.py`
- Modify: `src/civ_mcp/launcher_cli.py`
- Create: `src/civ_mcp/arena/benchmark_deploy.py`
- Modify: `tests/test_game_launcher.py`
- Modify: `tests/test_launcher_cli.py`
- Create: `tests/arena/test_benchmark_deploy.py`

**Interfaces:**
- Produces: `deploy_benchmark_save(source, save_name, expected_sha256) -> dict`, `wait_for_boot_health(profile_path, start_offset, min_frame, timeout_s) -> dict`, CLI commands `install-save` and `boot-health`, plus WSL bridge functions `deploy_via_windows(...) -> DeploymentEvidence` and `check_boot_health_via_windows(...) -> BootHealthEvidence`.

- [ ] **Step 1: Add source/destination hash and CLI tests**

```python
def test_deploy_benchmark_save_atomically_replaces_and_verifies(monkeypatch, tmp_path):
    source = tmp_path / "archive.Civ6Save"
    source.write_bytes(b"canonical")
    saves = tmp_path / "Single"
    saves.mkdir()
    monkeypatch.setattr(game_launcher, "SINGLE_SAVE_DIR", str(saves))
    digest = hashlib.sha256(b"canonical").hexdigest()

    result = game_launcher.deploy_benchmark_save(source, "BUILDER_ECONOMY_CAL_V1", digest)

    assert (saves / "BUILDER_ECONOMY_CAL_V1.Civ6Save").read_bytes() == b"canonical"
    assert result["deployed_sha256"] == digest
```

Add failures for source hash mismatch, unsafe save names, and post-copy mismatch. Add boot-health tests with an appendable `Profile.csv` fixture proving: new rows advance past frame 100; stale pre-offset rows do not count; a frame-3/5 stall times out with structured evidence; malformed/rotated logs fail closed.

- [ ] **Step 2: Run launcher/deployment tests and verify RED**

Run: `uv run pytest -q tests/test_game_launcher.py tests/test_launcher_cli.py tests/arena/test_benchmark_deploy.py`

Expected: FAIL because deployment/boot-health functions and CLI commands are absent.

- [ ] **Step 3: Add atomic native deployment and structured CLI output**

Validate save names with `^[A-Za-z0-9_-]+$`, hash in 1 MiB chunks, copy to a `NamedTemporaryFile` in `SINGLE_SAVE_DIR`, `flush`/`fsync`, then `os.replace`. Add this argparse contract:

```python
install = commands.add_parser("install-save")
install.add_argument("--archive", required=True)
install.add_argument("--name", required=True)
install.add_argument("--sha256", required=True)
install.add_argument("--json", action="store_true")
```

The WSL bridge calls the existing signed Windows bootstrap, parses one JSON object, and verifies `archive_sha256 == deployed_sha256 == expected_sha256`.

Add a `boot-health` subcommand that records the current byte offset of the native `AppData\Local\Firaxis Games\...\Logs\Profile.csv`, then polls only subsequently appended complete rows. It passes when the parsed frame counter exceeds 100 before the default 240-second deadline and returns the baseline offset, last frame, elapsed time, file identity, and native path. File truncation/rotation, no new complete row, or timeout is a structured failure. The runner calls this once at session startup; it does not silently kill/relaunch the game.

- [ ] **Step 4: Run launcher/bootstrap/deployment tests**

Run: `uv run pytest -q tests/test_game_launcher.py tests/test_launcher_cli.py tests/test_windows_launcher_bootstrap.py tests/arena/test_benchmark_deploy.py`

Expected: PASS, including counterfactual tests where using pre-offset rows or lowering the observed frame leaves the boot-health assertion failing.

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/game_launcher.py src/civ_mcp/launcher_cli.py src/civ_mcp/arena/benchmark_deploy.py tests/test_game_launcher.py tests/test_launcher_cli.py tests/arena/test_benchmark_deploy.py
git commit -m "feat(launcher): gate benchmark saves and game boot health"
```

---

### Task 7: Canonical queried-state capture

**Files:**
- Create: `src/civ_mcp/lua/benchmark.py`
- Modify: `src/civ_mcp/lua/__init__.py`
- Create: `src/civ_mcp/arena/benchmark_state.py`
- Create: `tests/arena/test_benchmark_state.py`

**Interfaces:**
- Produces: `build_benchmark_state_query(player_id, tile_coords)`, `parse_benchmark_state`, `capture_canonical_state`, `state_digest`, and `diff_state`.

- [ ] **Step 1: Add parser, normalization, and severing tests**

```python
def test_state_digest_is_order_independent_but_changes_for_declared_tile():
    unit_1 = {"id": 1, "type": "UNIT_BUILDER", "x": 8, "y": 8, "charges": 2}
    unit_2 = {"id": 2, "type": "UNIT_BUILDER", "x": 9, "y": 8, "charges": 1}
    mined = {"x": 9, "y": 8, "improvement": "IMPROVEMENT_MINE",
             "feature": None, "resource": "RESOURCE_IRON", "pillaged": False}
    left = {"turn": 157, "player_id": 0, "units": [unit_2, unit_1], "cities": [],
            "tiles": [mined]}
    right = {"turn": 157, "player_id": 0, "units": [unit_1, unit_2], "cities": [],
             "tiles": [mined]}
    assert state_digest(left) == state_digest(right)

    changed = {**right, "tiles": [{**mined, "improvement": None}]}
    assert state_digest(left) != state_digest(changed)
    assert diff_state(left, changed)["tiles[9,8].improvement"] == ["IMPROVEMENT_MINE", None]
```

- [ ] **Step 2: Run state tests and verify RED**

Run: `uv run pytest -q tests/arena/test_benchmark_state.py`

Expected: collection fails because the modules do not exist.

- [ ] **Step 3: Add one sentinel-framed Lua query and canonicalizer**

The Lua query prints game identity, turn, active player, gold/faith, every local unit, every local city, and only manifest-declared tiles. The parser returns JSON-safe primitives. The canonicalizer sorts units/cities by ID and tiles by `(x, y)` before hashing canonical JSON.

```python
def state_digest(state: Mapping[str, object]) -> str:
    normalized = normalize_state(state)
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run state and Lua export tests**

Run: `uv run pytest -q tests/arena/test_benchmark_state.py tests/test_lua_queries.py`

Expected: PASS. Perform a severing check by removing the tile rows from the fixture and confirm the expected digest assertion fails before restoring them.

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/lua/benchmark.py src/civ_mcp/lua/__init__.py src/civ_mcp/arena/benchmark_state.py tests/arena/test_benchmark_state.py
git commit -m "feat(arena): capture canonical benchmark game state"
```

---

### Task 8: Append-only session and raw-evidence store

**Files:**
- Create: `src/civ_mcp/arena/benchmark_store.py`
- Create: `tests/arena/test_benchmark_store.py`

**Interfaces:**
- Produces: `BenchmarkStore.create`, `open`, `append_event`, `record_attempt`, `commit_trial`, `completed_indices`, and `next_incomplete`.

- [ ] **Step 1: Add atomicity/resume tests**

```python
def test_scoreable_raw_trial_prevents_reexecution_even_without_report(tmp_path):
    lock = {"session_fingerprint": "abc123", "schedule_fingerprint": "def456"}
    store = BenchmarkStore.create(tmp_path / "run", lock)
    store.commit_trial(1, {"session_fingerprint": store.fingerprint, "terminal": "step_limit"})

    reopened = BenchmarkStore.open(tmp_path / "run", lock)

    assert reopened.completed_indices() == {1}
    assert reopened.next_incomplete([1, 2, 3]) == 2
    with pytest.raises(TrialExistsError):
        reopened.commit_trial(1, {"session_fingerprint": store.fingerprint})
```

Add tests for lock mismatch, incomplete temp files, attempts never counting as trials, and attempt counts surviving resume.

- [ ] **Step 2: Run store tests and verify RED**

Run: `uv run pytest -q tests/arena/test_benchmark_store.py`

Expected: collection fails because `BenchmarkStore` is undefined.

- [ ] **Step 3: Implement fsync-plus-replace writes and append-only JSONL**

`commit_trial` writes `trials/.trial-NNN.json.tmp`, flushes/fsyncs, refuses an existing destination, then calls `os.replace`. The lock is canonical JSON and must match byte-for-byte on open. Journal events include monotonic sequence, UTC timestamp, trial index, attempt ordinal, event, and failure class.

- [ ] **Step 4: Run crash-boundary tests**

Run: `uv run pytest -q tests/arena/test_benchmark_store.py`

Expected: PASS. Monkeypatch `os.replace` to raise and verify reopening sees no completed trial and retains the attempt record.

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/benchmark_store.py tests/arena/test_benchmark_store.py
git commit -m "feat(arena): persist append-only benchmark evidence"
```

---

### Task 9: Objective-blind single-turn agent

**Files:**
- Create: `src/civ_mcp/arena/benchmark_agent.py`
- Create: `tests/arena/test_benchmark_agent.py`

**Interfaces:**
- Produces: `BENCHMARK_SYSTEM`, `FINISH_TRIAL_SCHEMA`, `EpisodeTerminal`, and `SingleTurnAgent.run(gs, player_id, turn) -> EpisodeEvidence`.
- Consumes: `OpenAICompatBackend`, arena registry dispatch, tool tier, sampling already bound to backend, and derived episode wall.

- [ ] **Step 1: Add prompt, control-tool, and terminal tests**

```python
def test_prompt_is_objective_blind_and_control_surface_is_common():
    prompt = benchmark_prompt(turn=157, player_id=0)
    assert prompt == (
        "It is turn 157. You control player 0. Assess the current situation and "
        "issue the best orders available for this turn. When finished, call finish_trial."
    )
    forbidden = {"builder", "luxury", "repair", "9,10", "rubric"}
    assert not any(word in prompt.lower() for word in forbidden)
    assert resolved_benchmark_tools("minimal")[-1]["function"]["name"] == "finish_trial"
    assert resolved_benchmark_tools("standard")[-1]["function"]["name"] == "finish_trial"
```

Add async tests for explicit finish, implicit no-tool finish, step limit, multiple game calls plus finish in one response, and no `end_turn` schema.

- [ ] **Step 2: Run agent tests and verify RED**

Run: `uv run pytest -q tests/arena/test_benchmark_agent.py`

Expected: collection fails because the benchmark agent is missing.

- [ ] **Step 3: Implement the isolated tool loop**

Resolve the selected arena tier, reject `end_turn`, append `FINISH_TRIAL_SCHEMA`, and dispatch only game tools through `arena.registry.dispatch`. Record the same complete step fields as `LLMPolicy`, plus the manifest-scoped canonical state projection and digest before/after every mutation. These harness-only verification queries are not added to the model conversation. Stop after processing all game calls in a response containing `finish_trial`.

Use `asyncio.timeout(episode_wall_s)` around the complete episode and raise typed `EpisodeTimedOut` so the runner can execute the health discriminator.

- [ ] **Step 4: Run agent and registry tests**

Run: `uv run pytest -q tests/arena/test_benchmark_agent.py tests/arena/test_agent.py tests/arena/test_registry.py`

Expected: PASS and existing arena prompts remain byte-for-byte unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/benchmark_agent.py tests/arena/test_benchmark_agent.py
git commit -m "feat(arena): add objective-blind single-turn benchmark agent"
```

---

### Task 10: Startup and treatment gates

**Files:**
- Create: `src/civ_mcp/arena/benchmark_gates.py`
- Create: `tests/arena/test_benchmark_gates.py`

**Interfaces:**
- Produces: `GateFailure`, `check_clean_checkout`, `check_treatment_can_fire`, `check_gpu_conflicts`, `build_session_lock`, and `admit_model_block`.

- [ ] **Step 1: Add fail-closed gate tests**

```python
def test_calibration_gate_requires_minimal_progress_and_standard_completion():
    position = PositionManifest(
        position_id="builder-cal-v1",
        version=1,
        archive="benchmarks/saves/BUILDER_ECONOMY_CAL_V1.Civ6Save",
        archive_sha256="a" * 64,
        game_save_name="BUILDER_ECONOMY_CAL_V1",
        player_id=0,
        expected_state={},
        expected_state_sha256="b" * 64,
        relevant_tiles=((9, 8), (10, 8), (11, 8)),
        objectives=({"task_id": "repair", "unit_index": 4, "target": [9, 8]},),
        rubric=({"task_id": "repair", "levels": [0, 1, 2, 3, 4]},),
        split="calibration",
    )
    with pytest.raises(GateFailure, match="minimal.*levels 1-2"):
        check_treatment_can_fire(
            position=position,
            minimal_observation={"discoverable_task_ids": []},
            standard_capabilities={"improve_tile", "repair_improvement", "remove_feature"},
        )
```

Add tests for dirty WSL, Windows commit mismatch, missing/failed fresh-offset boot-health evidence, end-turn exposure, missing finish control, a counted backend with more than one hidden request attempt, zero briefing budget, endpoint identity mismatch, GPU conflict without scoped acknowledgment, and canonical-state mismatch.

- [ ] **Step 2: Run gate tests and verify RED**

Run: `uv run pytest -q tests/arena/test_benchmark_gates.py`

Expected: collection fails because gate functions are absent.

- [ ] **Step 3: Implement explicit evidence-returning gates**

Every gate returns JSON-safe evidence or raises `GateFailure(code, details)`. `build_session_lock` includes Git commits/status, fresh-offset boot-health evidence, manifest/schedule/prompt/rubric/tool/scorer digests, deployment evidence, canonical state, endpoint/model/GPU topology, exact sampling, seed verdict, ten warm latencies, p95, and derived wall.

`check_gpu_conflicts` never terminates a process. It returns conflicts and accepts only a set of operator-approved service names that exactly covers managed conflicts; unknown PIDs always fail.

- [ ] **Step 4: Run gate and config tests**

Run: `uv run pytest -q tests/arena/test_benchmark_gates.py tests/arena/test_config.py tests/arena/test_experiment.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/benchmark_gates.py tests/arena/test_benchmark_gates.py
git commit -m "feat(arena): fail closed on benchmark admission gates"
```

---

### Task 11: Serial runner, retry admission, and CLI

**Files:**
- Create: `src/civ_mcp/arena/benchmark_runner.py`
- Create: `tests/arena/test_benchmark_runner.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `FailureClass`, `RunnerDependencies`, `BenchmarkRunner.run`, CLI `civ-arena-benchmark --suite benchmarks/suites/builder-economy-calibration-v1.yaml --run-id builder-economy-calibration-smoke`, and exit codes 0 complete / 1 gate-or-session failure / 130 interrupted.

- [ ] **Step 1: Add serial orchestration and timeout-discriminator tests**

```python
@pytest.mark.asyncio
async def test_healthy_timeout_is_raw_trial_but_dead_endpoint_is_retry(tmp_path):
    class TimeoutAgent:
        async def run(self, gs, player_id, turn):
            raise EpisodeTimedOut("episode wall reached")

    health_results = iter([
        HealthProbe(healthy=True, identity_ok=True, latency_s=0.2, detail="ok"),
        HealthProbe(healthy=False, identity_ok=False, latency_s=10.0, detail="timeout"),
    ])

    async def probe_after_timeout():
        return next(health_results)

    canonical = {"turn": 157, "player_id": 0, "units": [], "cities": [], "tiles": []}
    store = BenchmarkStore.create(
        tmp_path / "run",
        {"session_fingerprint": "abc123", "schedule_fingerprint": "def456"},
    )
    deps = RunnerDependencies(
        reload_position=AsyncMock(return_value=None),
        dismiss_popups=AsyncMock(return_value="POPUPS|none"),
        capture_state=AsyncMock(return_value=canonical),
        make_agent=lambda _trial: TimeoutAgent(),
        probe_health=probe_after_timeout,
    )
    runner = BenchmarkRunner(store=store, dependencies=deps,
                             expected_state=canonical, player_id=0)

    def spec(index: int, arm_id: str) -> TrialSpec:
        return TrialSpec(
            index=index,
            pair_id="pair-001",
            position_id="builder-cal-v1",
            model="qwen3.6-27b",
            arm_id=arm_id,
            seed=101,
        )

    await runner.run_trial(spec(1, "minimal"))
    await runner.run_trial(spec(2, "standard"))

    assert runner.store.completed_indices() == {1}
    assert runner.store.attempt_count(2) == 1
    assert runner.store.trial(1)["terminal"] == "runaway_timeout"
```

Add tests proving strict serial execution, reload/popup/checksum order, checksum mismatch abort, three-attempt cap across process resume, zero-action/step-limit/malformed-output admission, reload/reconnect/popup/harness failures before episode completion as infrastructure attempts, immediate health classification of request/episode timeouts and transport failures, unknown exceptions stopping without retry, resume skipping raw trials, and scorer absence never replaying a trial.

- [ ] **Step 2: Run runner tests and verify RED**

Run: `uv run pytest -q tests/arena/test_benchmark_runner.py`

Expected: collection fails because `BenchmarkRunner` is missing.

- [ ] **Step 3: Implement one explicit state machine**

For each incomplete `TrialSpec`:

```text
deploy-confirmed → reload → continue/reconnect → popup hygiene
→ canonical checksum → fresh backend/agent → episode
→ final state → atomic raw trial
```

Encode the spec's retry table as explicit typed branches, not string matching or a broad catch-all. Reload/reconnect/popup failures and a harness crash before a complete episode are infrastructure attempts. On `EpisodeTimedOut`, request timeout, or transport failure, call `probe_health` immediately and journal both the original event and canary. Healthy/identity-correct admits a request/episode timeout as `runaway_timeout`; unhealthy or wrong identity records an infrastructure attempt and reloads on the next attempt. A healthy transport exception that is not a preregistered scoreable terminal and every unknown exception stop the session for classification instead of being retried.

Add entry point:

```toml
civ-arena-benchmark = "civ_mcp.arena.benchmark_runner:main"
```

- [ ] **Step 4: Run runner and lifecycle integration tests**

Run: `uv run pytest -q tests/arena/test_benchmark_runner.py tests/arena/test_benchmark_gates.py tests/arena/test_benchmark_store.py tests/test_game_lifecycle_load.py tests/arena/test_popup_dismiss.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/benchmark_runner.py tests/arena/test_benchmark_runner.py pyproject.toml uv.lock
git commit -m "feat(arena): run resumable controlled-position trials"
```

---

### Task 12: Derived scoring and deterministic reports

**Files:**
- Create: `src/civ_mcp/arena/benchmark_report.py`
- Create: `tests/arena/test_benchmark_report.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `score_rubric`, `score_trial`, `build_report`, `render_markdown`, `write_reports`, and CLI `civ-arena-benchmark-report <run-dir>`.
- Reuses: `action_metrics.evaluate_predicate`; scoring and action-quality classification must not maintain separate predicate semantics.
- Consumes: only `session.json`, `schedule.json`, and `trials/*.json`.

- [ ] **Step 1: Add report derivation and attempts-severing tests**

```python
import json


def test_report_ignores_attempts_and_weights_positions_equally(tmp_path):
    def write_json(path, payload):
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    run_dir = tmp_path / "run"
    (run_dir / "trials").mkdir(parents=True)
    (run_dir / "attempts").mkdir()
    write_json(run_dir / "session.json", {
        "session_fingerprint": "abc123",
        "scorer_fingerprint": "score-v1",
        "positions": {
            "easy": {"rubric": [{
                "task_id": "primary",
                "levels": [
                    {"score": 0, "predicate": {"kind": "always"}},
                    {"score": 1, "predicate": {
                        "kind": "final_state_equals",
                        "path": ["tiles", 0, "improvement"],
                        "value": "IMPROVEMENT_MINE",
                    }},
                ],
            }]},
            "hard": {"rubric": [{
                "task_id": "primary",
                "levels": [
                    {"score": 0, "predicate": {"kind": "always"}},
                    {"score": 1, "predicate": {
                        "kind": "final_state_equals",
                        "path": ["tiles", 0, "improvement"],
                        "value": "IMPROVEMENT_MINE",
                    }},
                ],
            }]},
        },
    })
    write_json(run_dir / "schedule.json", {
        "trials": [
            {"index": 1, "position_id": "easy"},
            {"index": 2, "position_id": "easy"},
            {"index": 3, "position_id": "hard"},
        ],
    })
    for index, position_id, satisfied in (
        (1, "easy", True),
        (2, "easy", True),
        (3, "hard", False),
    ):
        write_json(run_dir / "trials" / f"trial-{index:03d}.json", {
            "index": index,
            "position_id": position_id,
            "attempt_count": 1,
            "terminal": "finish_trial",
            "steps": [],
            "initial_state": {"tiles": [{"improvement": None}]},
            "final_state": {"tiles": [{
                "improvement": "IMPROVEMENT_MINE" if satisfied else None,
            }]},
        })
    write_json(run_dir / "attempts" / "attempt-002-001.json", {
        "trial_index": 2,
        "failure_class": "gateway_unavailable",
    })

    report = build_report(run_dir)
    assert report["aggregate"]["equal_weight_mean"] == 0.5

    (run_dir / "attempts" / "noise.json").write_text("{\"rubric_score\": 1.0}")
    assert build_report(run_dir) == report
```

Add tests for 10/12 calibration wins, median delta 4, ties as non-wins, per-position output, retry counts copied from raw trials, scorer fingerprint, and byte-identical regeneration.

- [ ] **Step 2: Run report tests and verify RED**

Run: `uv run pytest -q tests/arena/test_benchmark_report.py`

Expected: collection fails because report functions are absent.

- [ ] **Step 3: Implement pure evidence-to-report transforms**

Use the shared versioned, fail-closed predicate vocabulary over raw calls and queried state. Score each task at the highest satisfied preregistered level. An unknown predicate kind, missing path, or malformed rubric aborts report generation instead of scoring zero. Add report-level tests proving the shared evaluator is used rather than reimplemented.

Normalize each position independently, aggregate per-position medians with equal weight, and render every position before aggregates. Include action quality, attempts, terminal conditions, seeds/seed-support, endpoint topology, latency, tokens, and cost. The scorer derives predicate truth from raw `steps`, `initial_state`, and `final_state`; raw trial artifacts never contain scorer-produced points or pass/fail labels.

Add entry point:

```toml
civ-arena-benchmark-report = "civ_mcp.arena.benchmark_report:main"
```

Write `report.json` with canonical JSON and `report.md` from that mapping. Neither function imports or scans `attempts/`.

- [ ] **Step 4: Run report tests twice and compare hashes**

Run: `uv run pytest -q tests/arena/test_benchmark_report.py tests/arena/test_action_metrics.py`

Expected: PASS. Generate the same fixture report twice and verify identical SHA-256 values.

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/benchmark_report.py tests/arena/test_benchmark_report.py pyproject.toml uv.lock
git commit -m "feat(arena): derive controlled benchmark reports"
```

---

### Task 13: Full verification and software handoff

**Files:**
- Modify only files required by failures found in this task.
- Create after verification: `docs/superpowers/plans/2026-08-30-arena-benchmark-position-authoring.md` in a separate planning session after live position inspection.

**Interfaces:**
- Produces: a clean, committed runner ready for live save/rubric authoring.

- [ ] **Step 1: Run the complete focused benchmark/launcher suite**

Run:

```bash
uv run pytest -q \
  tests/arena/test_action_metrics.py \
  tests/arena/test_benchmark_backend.py \
  tests/arena/test_benchmark_manifest.py \
  tests/arena/test_benchmark_schedule.py \
  tests/arena/test_benchmark_state.py \
  tests/arena/test_benchmark_store.py \
  tests/arena/test_benchmark_agent.py \
  tests/arena/test_benchmark_gates.py \
  tests/arena/test_benchmark_runner.py \
  tests/arena/test_benchmark_report.py \
  tests/test_game_lifecycle_load.py \
  tests/test_game_launcher.py \
  tests/test_launcher_cli.py \
  tests/test_windows_launcher_bootstrap.py \
  tests/arena/test_popup_dismiss.py
```

Expected: all tests pass.

- [ ] **Step 2: Run the full repository suite**

Run: `uv run pytest -q -p no:cacheprovider`

Expected: all tests pass with no new warnings or hangs.

- [ ] **Step 3: Verify the CLI contracts without touching the live game**

Run:

```bash
uv run civ-arena-benchmark --help
uv run civ-arena-benchmark-report --help
```

Expected: both exit 0 and show required suite/run-directory arguments plus the scoped GPU-drain acknowledgment option.

- [ ] **Step 4: Run final provenance checks**

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors and only intentional plan-execution changes before the final commit.

- [ ] **Step 5: Commit any final integration-only corrections**

If Step 1–4 required no correction, do not create an empty commit. If a correction was necessary:

```bash
git add src/civ_mcp tests pyproject.toml uv.lock
git commit -m "fix(arena): close benchmark runner integration gaps"
```

After this plan is complete, inspect the live game and write the separate position-authoring/campaign plan with exact save names, canonical state, unit IDs, coordinates, rubric predicates, audit indices, sampling values, and seed lists. Do not begin counted calibration from generic fixture manifests.
