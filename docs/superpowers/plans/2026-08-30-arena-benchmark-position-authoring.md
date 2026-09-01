# Arena Benchmark Counted-Calibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the smoke-proven single-turn runner into a gated, two-model counted calibration campaign, freeze one reproducible builder-economy position, and publish trustworthy evidence that the instrument detects the known minimal-versus-standard tool-surface contrast.

**Architecture:** Add a campaign lifecycle around the reviewed `BenchmarkRunner`; do not rewrite its serial trial loop, retry taxonomy, journal, or atomic evidence store. A campaign lock owns shared protocol identity, the existing session lock evolves into one fresh per-model block lock, and every counted trial carries both fingerprints. Live admission gathers fresh checkout, boot, tuner, save, GPU, endpoint, model, tool-call, latency, and treatment evidence immediately before each model block. Position authoring and verification call the same reload and canonical-state functions as counted trials. Reports are deterministic derivations from locks and `trials/` only.

**Tech Stack:** Python 3.12, dataclasses, PyYAML, `asyncio`, OpenAI-compatible local gateways, FireTuner, Windows launcher bridge, pytest/pytest-asyncio, Git.

**Spec:** [`docs/superpowers/specs/2026-08-31-arena-benchmark-calibration-campaign-design.md`](../specs/2026-08-31-arena-benchmark-calibration-campaign-design.md)

## Global Constraints

- Preserve `BenchmarkRunner.run`, `run_trial`, its retry classifications, append-only attempt journal, and atomic trial commit path. The campaign layer supplies a fully resolved block; it does not become a second runner.
- FireTuner port `4318` is single-client. All admission, reload verification, validation, and counted trials are strictly serial.
- `end_turn` is absent in both arms. `finish_trial` is present in both arms. Plan 2 exercises only `options: {}`; non-empty arm treatments remain Plan 3 work.
- Counted evidence is possible only when both a campaign fingerprint and a fresh per-model session fingerprint exist. `--ungated-smoke`, `--admit-only`, and non-counting validation never mint that pair.
- Use TDD for every code change: add the focused failing test, run it and observe the expected failure, implement the minimum change, rerun it, then run the neighboring suite. For invariants, mutate or temporarily revert the implementation to prove the test can fail before keeping it.
- Do not use counted transcripts to revise the prompt, rubric, predicate vocabulary, or scorer. Pre-counting validation may reveal defects, but each revision requires a new version/fingerprint and a fresh campaign lock before counting begins.
- Never silently kill a process or drain a model service. Unknown tuner/GPU owners always block. A requested targeted action must revalidate the exact PID/service identity immediately before acting and must re-run the admission check afterward.
- Reports read campaign/block locks, schedules, audits, and `trials/`; they never read `attempts/`. They contain no generation-time timestamp, so regeneration is byte-identical.

## File map

| Area | Files | Responsibility |
|---|---|---|
| Existing trusted core | `benchmark_runner.py`, `benchmark_store.py`, `benchmark_agent.py` | Serial trials, retries, raw episode evidence, atomic trial commits |
| Protocol/config | `benchmark_manifest.py`, new `benchmark_contract.py` | Strict YAML loading, contract versions, campaign/model configuration, fingerprints |
| Campaign lifecycle | new `benchmark_campaign.py`, new `benchmark_admission.py` | Campaign/block locks, fresh live admission, resume, execution handoff |
| Live evidence | new `benchmark_live_evidence.py`, `benchmark_deploy.py` | Checkout, tuner ownership, GPU/process evidence, save deployment, boot health |
| Game reload | `game_launcher.py`, `launcher_cli.py`, `tools/windows/civ6_launcher_bootstrap.py` | Positive screen classification, safe continuation, native boot bridge |
| Position freeze | new `benchmark_position.py`, `benchmark_state.py`, `action_metrics.py` | Production-path capture/verification and safe rubric predicates |
| Reporting | `benchmark_report.py`, new `benchmark_campaign_report.py` | Per-block scoring, audit/tie attribution, campaign verdict, deterministic output |
| Frozen artifacts | `benchmarks/contracts/`, `benchmarks/positions/`, `benchmarks/saves/`, `benchmarks/provenance/`, `benchmarks/campaigns/` | Versioned instrument, position, save, authoring journal, counted campaign manifest |

---

### Task 1: Make manifest failures typed and field-specific

**Files:**

- Modify: `src/civ_mcp/arena/benchmark_manifest.py`
- Modify: `tests/arena/test_benchmark_manifest.py`

**Interfaces:**

- Consumes: existing `PositionManifest`, `SuiteManifest`, `TreatmentArm`, and `SamplingConfig` dataclasses.
- Produces: unchanged public `load_position_manifest(path) -> PositionManifest` and `load_suite_manifest(path) -> SuiteManifest` signatures with field-specific `ValueError` failures.

- [ ] **Step 1: Add failing tests for YAML nulls and wrong scalar/container types**

Add parameterized cases for every collection that currently executes `tuple(None)` or `dict(None)`, plus representative scalar failures:

```python
@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("relevant_tiles", None, "position manifest.relevant_tiles must be a list"),
        ("expected_state", None, "position manifest.expected_state must be a mapping"),
        ("arms", None, "suite manifest.arms must be a list"),
        ("seeds", None, "suite manifest.seeds must be a list"),
        ("max_steps", None, "suite manifest.max_steps must be an integer"),
        ("sampling", None, "suite manifest.sampling must be a mapping"),
    ],
)
def test_manifest_nulls_raise_field_specific_value_errors(...): ...
```

Also assert that booleans are rejected where an integer is required, every tile is exactly two integers, seeds/audit indices are integers, strings are non-empty, and sampling values are either their declared numeric type or `None`.

- [ ] **Step 2: Run the focused tests and confirm the existing bare `TypeError`/unclear failure**

Run: `uv run pytest -q tests/arena/test_benchmark_manifest.py`

Expected: new cases fail because loaders currently call constructors on unchecked YAML values.

- [ ] **Step 3: Add reusable strict field readers and use them in both loaders**

Keep unknown/missing-key rejection, then validate before conversion:

```python
def _require_mapping(value: object, field: str) -> Mapping[str, object]: ...
def _require_list(value: object, field: str) -> list[object]: ...
def _require_str(value: object, field: str) -> str: ...
def _require_int(value: object, field: str, *, minimum: int | None = None) -> int: ...
def _require_optional_number(value: object, field: str) -> float | None: ...
```

All public loader errors remain `ValueError`; messages begin with the exact manifest and field name. Do not add permissive defaults.

- [ ] **Step 4: Verify the focused and neighboring manifest/schedule tests**

Run: `uv run pytest -q tests/arena/test_benchmark_manifest.py tests/arena/test_benchmark_schedule.py`

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/benchmark_manifest.py tests/arena/test_benchmark_manifest.py
git commit -m "fix(benchmark): reject malformed manifest values explicitly"
```

---

### Task 2: Define versioned campaign contracts and exact tool identities

**Files:**

- Create: `src/civ_mcp/arena/benchmark_contract.py`
- Create: `tests/arena/test_benchmark_contract.py`
- Modify: `src/civ_mcp/arena/benchmark_agent.py`
- Modify: `tests/arena/test_benchmark_agent.py`

**Interfaces:**

- Consumes: Task 1 strict field readers, `resolved_benchmark_tools`, `BENCHMARK_SYSTEM`, `SamplingConfig`, `RetryPolicy`.
- Produces: `ContractVersions`, `ModelBlockConfig`, `CalibrationRules`, `CampaignManifest`, `load_campaign_manifest`, `suite_for_block`, `tool_surface_identity`, `tool_input_identity`, `scorer_source_fingerprint`, `write_contract_candidate`, and injected `SingleTurnAgent.user_prompt`.

- [ ] **Step 1: Write failing tests for contract parsing and the two tool fingerprints**

Pin these distinctions:

```python
def test_tool_surface_fingerprint_ignores_schema_text_changes(): ...
def test_tool_input_fingerprint_changes_when_description_or_parameters_change(): ...
def test_contract_versions_require_four_nonempty_version_fields(): ...
def test_campaign_manifest_rejects_models_without_sampling_and_chat_template_kwargs(): ...
def test_campaign_manifest_rejects_nonempty_arm_options_in_plan2(): ...
def test_objective_blind_prompt_has_one_frozen_digest_for_every_position(): ...
async def test_single_turn_agent_uses_injected_frozen_prompt_verbatim(): ...
```

The input-fingerprint mutation test must alter one nested required argument and prove the digest changes. The surface test must alter descriptions while leaving ordered tool names/capability IDs unchanged and prove the surface digest does not change.

- [ ] **Step 2: Run the tests and confirm imports/types are absent**

Run: `uv run pytest -q tests/arena/test_benchmark_contract.py tests/arena/test_benchmark_agent.py`

Expected: contract imports fail and the new fingerprint assertions cannot run.

- [ ] **Step 3: Add immutable contract and campaign dataclasses**

Define and strictly load:

```python
@dataclass(frozen=True)
class ContractVersions:
    evidence_schema_version: str
    predicate_schema_version: str
    report_schema_version: str
    scorer_fingerprint: str

@dataclass(frozen=True)
class ModelBlockConfig:
    block_id: str
    model: str
    endpoint_id: str
    sampling: SamplingConfig
    chat_template_kwargs: dict[str, object]
    briefing_required: bool

@dataclass(frozen=True)
class CalibrationRules:
    pairs_per_model: int
    minimum_decided_pairs: int
    minimum_standard_wins: int
    minimum_median_normalized_delta: float
    required_audits_per_arm: int

@dataclass(frozen=True)
class CampaignManifest:
    campaign_id: str
    campaign_schema_version: str
    position: str
    position_provenance: str
    models: tuple[ModelBlockConfig, ...]
    arms: tuple[TreatmentArm, ...]
    seeds: tuple[int, ...]
    order: str
    driver: str
    fresh_conversation_per_trial: bool
    retry_policy: RetryPolicy
    max_steps: int
    result_char_cap: int
    audit_indices: tuple[int, ...]
    prompt: str
    contracts: ContractVersions
    rules: CalibrationRules
```

`load_campaign_manifest` resolves the contract and provenance paths relative to the campaign YAML and verifies the provenance digest. It must require exactly two Plan-2 arms (`minimal`, `standard` tool tiers, empty `options`), Gemma before Qwen, one position, 12 seeds, ABBA, `single_turn`, fresh context true, `RetryPolicy(max_attempts=1)`, 8 max steps, and six balanced local audit indices. Add `suite_for_block(campaign, model_block)` to construct the existing one-model `SuiteManifest`; each block therefore has local trial indices 1–24 and the same balanced audits. Keep those campaign-specific restrictions here rather than weakening reusable `SuiteManifest` rules.

- [ ] **Step 4: Expose exact resolved schemas once and fingerprint them canonically**

Add a public agent helper that returns the already benchmark-safe tier schema (no `end_turn`, with `finish_trial`). Extend `SingleTurnAgent` with an optional immutable `user_prompt`; when present, `_run_episode` uses it verbatim, and when absent the existing smoke/legacy `benchmark_prompt(turn, player_id)` behavior remains. The campaign prompt fingerprint covers both `BENCHMARK_SYSTEM` and the frozen injected user prompt. In `benchmark_contract.py` implement:

```python
def tool_surface_identity(tools_by_arm: Mapping[str, Sequence[Mapping[str, object]]]) -> dict:
    # arm -> ordered [{"name": ..., "capability_id": ToolDef.requires}]

def tool_input_identity(tools_by_arm: Mapping[str, Sequence[Mapping[str, object]]]) -> dict:
    # arm -> canonical full function schemas

def fingerprint_identity(value: object) -> str:
    return fingerprint(value)

def scorer_source_fingerprint(repo_root: Path) -> str:
    # canonical digest of action_metrics.py, benchmark_report.py, and
    # benchmark_campaign_report.py paths plus file bytes

def write_contract_candidate(path: Path, versions: ContractVersions, repo_root: Path) -> dict: ...
```

Give `benchmark_contract.py` a `python -m` `freeze` subcommand that accepts the three semantic versions and output path, computes the exact scorer source fingerprint, and writes the candidate atomically. A missing scorer source produces a field-specific error rather than a partial contract.

The surface identity omits descriptions and JSON schemas; the input identity includes them verbatim. Fail if a resolved schema exposes `end_turn` or omits `finish_trial`.

- [ ] **Step 5: Verify with counterfactual mutations**

Run: `uv run pytest -q tests/arena/test_benchmark_contract.py tests/arena/test_benchmark_agent.py`

Then temporarily include `description` in the surface identity and confirm `test_tool_surface_fingerprint_ignores_schema_text_changes` fails; revert the temporary mutation and rerun.

Expected: all tests pass after the revert.

- [ ] **Step 6: Commit**

```bash
git add src/civ_mcp/arena/benchmark_contract.py src/civ_mcp/arena/benchmark_agent.py tests/arena/test_benchmark_contract.py tests/arena/test_benchmark_agent.py
git commit -m "feat(benchmark): version campaign contracts and tool identities"
```

---

### Task 3: Add the campaign lock/store without adding a third identity

**Files:**

- Create: `src/civ_mcp/arena/benchmark_campaign.py`
- Create: `tests/arena/test_benchmark_campaign.py`
- Modify: `src/civ_mcp/arena/benchmark_gates.py`
- Modify: `tests/arena/test_benchmark_gates.py`
- Modify: `src/civ_mcp/arena/benchmark_store.py`
- Modify: `tests/arena/test_benchmark_store.py`

**Interfaces:**

- Consumes: Task 2 `CampaignManifest`, `suite_for_block`, tool identities, existing `compile_schedule`, `BenchmarkStore`, and `build_session_lock` evidence checks.
- Produces: `compile_campaign_schedule(campaign) -> dict`, `build_campaign_lock(...) -> dict`, `CampaignStore`, and an evolved `BenchmarkStore`/`session.json` contract with both fingerprints.

- [ ] **Step 1: Write failing tests for the two-level artifact layout**

Cover creation, reopen, mismatch, and absence:

```python
def test_campaign_store_creates_campaign_schedule_admissions_and_blocks(): ...
def test_campaign_store_reopen_requires_byte_identical_campaign_lock(): ...
def test_campaign_schedule_contains_two_local_24_trial_block_schedules(): ...
def test_block_store_requires_campaign_and_session_fingerprints(): ...
def test_existing_block_trial_with_wrong_campaign_fingerprint_is_not_complete(): ...
def test_session_lock_evolves_to_block_lock_without_new_lock_artifact(): ...
```

Expected layout:

```text
benchmark_runs/<campaign-id>/
  campaign.json
  schedule.json
  campaign-journal.jsonl
  admissions/<block-id>-attempt-NNN.json
  blocks/<block-id>/session.json
  blocks/<block-id>/schedule.json
  blocks/<block-id>/journal.jsonl
  blocks/<block-id>/attempts/
  blocks/<block-id>/trials/
```

- [ ] **Step 2: Run the tests and observe missing campaign types/validation**

Run: `uv run pytest -q tests/arena/test_benchmark_campaign.py tests/arena/test_benchmark_store.py tests/arena/test_benchmark_gates.py`

Expected: the new module/API does not exist.

- [ ] **Step 3: Implement `CampaignStore` as a small wrapper**

Use the canonical/fsync write behavior already proven in `benchmark_store.py`; do not duplicate trial storage:

```python
class CampaignStore:
    @classmethod
    def create(cls, root: Path, campaign_lock: dict, schedule: dict) -> "CampaignStore": ...
    @classmethod
    def open(cls, root: Path, campaign_lock: dict, schedule: dict) -> "CampaignStore": ...
    def record_admission(self, block_id: str, evidence: dict) -> Path: ...
    def open_block(self, block_id: str, session_lock: dict, schedule: dict) -> BenchmarkStore: ...
```

`record_admission` allocates the next append-only `admissions/<block-id>-attempt-NNN.json`; it never overwrites a prior attempt. `open_block` passes the existing block directory to `BenchmarkStore`; it does not create another lock file.

Add `compile_campaign_schedule(campaign)` returning a canonical `{"blocks": {block_id: {"trials": [...]}}}` payload. It calls the existing `compile_schedule(suite_for_block(...))` once per model, preserving local indices 1–24; it never renumbers or reorders those block schedules.

- [ ] **Step 4: Build the campaign lock from every shared frozen input**

Implement:

```python
def build_campaign_lock(
    campaign: CampaignManifest,
    position: PositionManifest,
    position_provenance: Mapping[str, object],
    schedule: Mapping[str, object],
    *,
    expected_commit: str,
    prompt_fingerprint: str,
    rubric_fingerprint: str,
    tool_surface_fingerprint: str,
) -> dict[str, object]: ...
```

The lock includes campaign schema version, the non-empty clean WSL commit expected on both checkouts, save archive/state/provenance digests, environment identity, full rubric/objectives, prompt, arms, seeds/order, driver/fresh-context rule, model configurations, retry policy, audit indices, calibration rules, all contract versions, scorer fingerprint, and the tool-surface identity. It deliberately does **not** include exact input schemas: schema text is block admission evidence, so it changes `session_fingerprint` without rewriting the scientific campaign. Compute `campaign_fingerprint` over every other field. Reject any missing digest, non-empty treatment option, or mismatch between the manifest, compiled schedule, position, provenance, and contract candidate. Capture the expected commit after the campaign/config freeze commit; it must remain unchanged until both model blocks finish.

- [ ] **Step 5: Evolve `build_session_lock` into the per-model block lock**

Add required fields:

```python
campaign_fingerprint: str
block_id: str
model_config: Mapping[str, object]
admission_fingerprint: str
tool_surface_fingerprint: str
tool_input_fingerprint: str
episode_wall_s: int
```

Keep the filename `session.json` and key `session_fingerprint`. Remove shared campaign data that is now referenced through `campaign_fingerprint`; retain the position/rubric fields needed for standalone per-block derivation. Reject a missing campaign fingerprint for counted locks.

Add a counterfactual test proving a description-only schema edit preserves `campaign_fingerprint` but changes `session_fingerprint` through `tool_input_fingerprint`.

- [ ] **Step 6: Make `BenchmarkStore` enforce both stamps for counted blocks**

Store `campaign_fingerprint` alongside the existing `fingerprint`. `is_trial_complete` must parse an existing trial and return true only when both stored stamps match the lock. A corrupt, stale, copied, or single-stamped file is not silently skipped; surface a provenance error so the campaign stops for operator review.

- [ ] **Step 7: Verify with fingerprint mutation tests**

Run: `uv run pytest -q tests/arena/test_benchmark_campaign.py tests/arena/test_benchmark_store.py tests/arena/test_benchmark_gates.py`

Mutate a fixture's campaign fingerprint after creation and prove reopen/resume fails; restore it and rerun.

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/civ_mcp/arena/benchmark_campaign.py src/civ_mcp/arena/benchmark_gates.py src/civ_mcp/arena/benchmark_store.py tests/arena/test_benchmark_campaign.py tests/arena/test_benchmark_gates.py tests/arena/test_benchmark_store.py
git commit -m "feat(benchmark): add campaign and per-model block locks"
```

---

### Task 4: Extract a resolved-block handoff and structurally exclude smoke evidence

**Files:**

- Modify: `src/civ_mcp/arena/benchmark_runner.py`
- Modify: `src/civ_mcp/arena/benchmark_report.py`
- Modify: `tests/arena/test_benchmark_runner.py`
- Modify: `tests/arena/test_benchmark_report.py`

**Interfaces:**

- Consumes: Task 3 `CampaignStore`/block `BenchmarkStore`, existing `BenchmarkRunner`, `PositionManifest`, `SuiteManifest`, and `TrialSpec`.
- Produces: `ResolvedBlock`, `run_resolved_block(block) -> int`, dual-stamped counted trial payloads, and preserved single-stamped ungated-smoke diagnostics.

- [ ] **Step 1: Add failing preservation and provenance tests**

Pin the boundary before refactoring:

```python
async def test_run_resolved_block_delegates_to_existing_benchmark_runner(): ...
def test_finalize_trial_stamps_campaign_and_session_fingerprints(): ...
def test_counted_report_rejects_trial_missing_either_fingerprint(): ...
def test_smoke_lock_never_contains_campaign_fingerprint(): ...
def test_smoke_report_is_advisory_and_campaign_report_ineligible(): ...
def test_resume_rejects_copied_trial_from_another_campaign(): ...
```

Use a spy `BenchmarkRunner` and assert the exact compiled schedule is passed unchanged and in order.

- [ ] **Step 2: Run the focused tests and confirm the missing handoff/stamp failures**

Run: `uv run pytest -q tests/arena/test_benchmark_runner.py tests/arena/test_benchmark_report.py`

Expected: new tests fail; existing smoke tests remain green.

- [ ] **Step 3: Extract only the production assembly boundary**

Add:

```python
@dataclass(frozen=True)
class ResolvedBlock:
    position: PositionManifest
    suite: SuiteManifest
    schedule: tuple[TrialSpec, ...]
    store: BenchmarkStore
    gateway_url: str
    api_key: str
    episode_wall_s: int
    chat_template_kwargs: dict[str, object]
    user_prompt: str

async def run_resolved_block(block: ResolvedBlock) -> int: ...
```

This function connects, calls `_build_live_dependencies`, constructs the existing `BenchmarkRunner`, invokes `runner.run(block.schedule)`, and closes dependencies. It must not replicate or modify the trial loop. Change smoke `_run_async` to assemble a non-counted `ResolvedBlock` and call it.

- [ ] **Step 4: Thread resolved model settings without implementing Plan-3 options**

Pass `episode_wall_s` and the frozen `user_prompt` into every `SingleTurnAgent`, and `chat_template_kwargs` into every cached backend. Keep the fail-closed non-empty `arm.options` branch, but change its message to state that options are deferred to Plan 3 rather than this plan.

- [ ] **Step 5: Stamp and validate both identities at the existing finalization boundary**

In `_finalize_trial`, copy both fingerprints from the store lock into the payload. In report loading, require exact matches when `ungated_smoke` is false. For smoke, require `ungated_smoke: true`, omit `campaign_fingerprint`, and retain the existing warning; a campaign-level report will reject it in Task 11.

- [ ] **Step 6: Run focused tests and the counterfactual runner test**

Run: `uv run pytest -q tests/arena/test_benchmark_runner.py tests/arena/test_benchmark_report.py`

Temporarily remove the campaign stamp from `_finalize_trial`, verify the new test fails, restore it, and rerun.

Expected: all pass; the original retry/failure-classification tests are unchanged.

- [ ] **Step 7: Commit**

```bash
git add src/civ_mcp/arena/benchmark_runner.py src/civ_mcp/arena/benchmark_report.py tests/arena/test_benchmark_runner.py tests/arena/test_benchmark_report.py
git commit -m "refactor(benchmark): hand resolved blocks to the trusted runner"
```

---

### Task 5: Pin backend request configuration and prove structured tool capability

**Files:**

- Modify: `src/civ_mcp/arena/backends.py`
- Modify: `src/civ_mcp/arena/benchmark_backend.py`
- Modify: `src/civ_mcp/arena/benchmark_gates.py`
- Modify: `tests/arena/test_backends.py`
- Modify: `tests/arena/test_benchmark_backend.py`
- Modify: `tests/arena/test_benchmark_gates.py`

**Interfaces:**

- Consumes: Task 2 `ModelBlockConfig`, resolved per-arm schemas, existing `OpenAICompatBackend`, `BackendProbe`, and `admit_model_block`.
- Produces: configurable `OpenAICompatBackend.chat_template_kwargs`, `ToolCanaryEvidence`, `probe_tool_capability(...)`, and conditional briefing/tool-canary admission evidence including `episode_wall_s`.

- [ ] **Step 1: Add failing wire-format and canary tests**

Cover exact request forwarding and both canaries:

```python
async def test_backend_sends_locked_chat_template_kwargs_and_sampling(): ...
async def test_tool_canary_requires_finish_trial_call(): ...
async def test_tool_canary_requires_exact_move_unit_arguments_without_dispatching(): ...
async def test_tool_canary_rejects_text_only_and_malformed_json_replies(): ...
def test_admission_requires_canaries_for_every_arm(): ...
def test_admission_allows_zero_briefing_budget_when_briefing_is_off(): ...
def test_admission_requires_positive_budget_when_briefing_is_on(): ...
```

The required-argument canary asks for `move_unit` with the exact sentinel `{"unit_index": 7, "x": 11, "y": 13}`. The test must prove no dispatcher/game connection is called.

- [ ] **Step 2: Run the focused tests and observe the hardcoded request body/missing canaries**

Run: `uv run pytest -q tests/arena/test_backends.py tests/arena/test_benchmark_backend.py tests/arena/test_benchmark_gates.py`

Expected: new tests fail because `enable_thinking=False` is hardcoded and admission has no structured-call evidence.

- [ ] **Step 3: Make chat-template settings an immutable backend input**

Extend the constructor while preserving arena defaults:

```python
def __init__(..., chat_template_kwargs: Mapping[str, object] | None = None):
    self.chat_template_kwargs = dict(
        {"enable_thinking": False} if chat_template_kwargs is None else chat_template_kwargs
    )
```

Send a defensive copy in `extra_body`. Counted blocks always pass the exact mapping from `ModelBlockConfig`; ordinary arena callers retain current behavior.

- [ ] **Step 4: Implement two nondispatching structured-call probes**

Define JSON-safe evidence:

```python
@dataclass(frozen=True)
class ToolCanaryEvidence:
    arm_id: str
    finish_trial_ok: bool
    required_argument_ok: bool
    observed_calls: tuple[dict[str, object], ...]
    errors: tuple[str, ...]

async def probe_tool_capability(backend, *, arm_id: str, tools: list[dict]) -> ToolCanaryEvidence: ...
```

Use two fresh, generic prompts: one requires `finish_trial`, one requires the exact sentinel `move_unit`. Parse the returned JSON arguments and compare exact values. Validate only; never route the call to a tool handler.

- [ ] **Step 5: Tighten model admission without requiring a treatment that is off**

Change `admit_model_block` to accept:

```python
briefing_required: bool
briefing_budget_chars: int | None
tool_canaries: Mapping[str, ToolCanaryEvidence]
expected_arm_ids: Sequence[str]
```

Require both canaries for every arm. Only enforce `briefing_budget_chars > 0` when `briefing_required` is true; for this calibration it is false and the locked evidence explicitly records `briefing_budget_chars: null`. Keep retry, identity, seed, latency, and per-model wall-clock rules.

- [ ] **Step 6: Prove the gates fail counterfactually**

Run: `uv run pytest -q tests/arena/test_backends.py tests/arena/test_benchmark_backend.py tests/arena/test_benchmark_gates.py`

Then change the fake required-argument reply from `x=11` to `x=12` and confirm admission fails; restore and rerun.

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/civ_mcp/arena/backends.py src/civ_mcp/arena/benchmark_backend.py src/civ_mcp/arena/benchmark_gates.py tests/arena/test_backends.py tests/arena/test_benchmark_backend.py tests/arena/test_benchmark_gates.py
git commit -m "feat(benchmark): admit exact model configs and tool callers"
```

---

### Task 6: Fail closed on missing boot telemetry and disarm Escape at world-ready

**Files:**

- Modify: `src/civ_mcp/arena/benchmark_deploy.py`
- Modify: `src/civ_mcp/game_launcher.py`
- Modify: `src/civ_mcp/launcher_cli.py`
- Modify: `tools/windows/civ6_launcher_bootstrap.py`
- Modify: `tests/arena/test_benchmark_deploy.py`
- Modify: `tests/test_game_launcher.py`
- Modify: `tests/test_launcher_cli.py`

**Interfaces:**

- Consumes: existing Windows bridge, `BootHealthEvidence`, `wait_for_boot_health`, WinRT OCR/key helpers, and `continue_after_lua_load`.
- Produces: nullable `BootHealthEvidence.baseline_offset`, explicit missing-profile failures, `FrontendLoadState`, and a continuation path that sends Escape only on positive frontend evidence.

- [ ] **Step 1: Add failing tests for absent `Profile.csv` and Escape classification**

Tests must cover:

```python
def test_boot_health_preserves_absent_baseline_as_none(): ...
def test_boot_health_gate_rejects_missing_profile_csv(): ...
async def test_continue_after_lua_load_presses_escape_only_on_recognized_continue_screen(): ...
async def test_continue_after_lua_load_never_presses_escape_in_world(): ...
async def test_continue_after_lua_load_never_presses_escape_when_tuner_is_open(): ...
async def test_continue_after_lua_load_waits_on_unknown_screen(): ...
```

Use injected OCR/screen classifiers and key senders; no GUI is required.

- [ ] **Step 2: Run the focused tests and observe the current `0` default/periodic Escape behavior**

Run: `uv run pytest -q tests/arena/test_benchmark_deploy.py tests/test_game_launcher.py tests/test_launcher_cli.py`

Expected: new tests fail because `baseline_offset` defaults to `0` and `continue_after_lua_load` can press Escape without positive screen evidence.

- [ ] **Step 3: Preserve missing boot evidence end to end**

Change `BootHealthEvidence.baseline_offset` to `int | None`; parse with `payload.get("baseline_offset")`, never `0`. Native `wait_for_boot_health` must return an explicit error when `Profile.csv` is absent or has no readable baseline. The CLI bridge returns JSON with `ok: false`, `baseline_offset: null`, and an actionable `error`.

- [ ] **Step 4: Add a conservative frontend state classifier**

Use a small enum:

```python
class FrontendLoadState(str, Enum):
    CONTINUE_SCREEN = "continue_screen"
    LEADER_SCREEN = "leader_screen"
    IN_WORLD = "in_world"
    UNKNOWN = "unknown"
```

The classifier may use WinRT OCR and existing tuner-port state. `continue_after_lua_load` sends one scan-code Escape only for `CONTINUE_SCREEN` or `LEADER_SCREEN`. `IN_WORLD` or an open tuner permanently disarms the Escape waiter. `UNKNOWN` only polls until timeout. Include the final classification in the returned result string/evidence.

- [ ] **Step 5: Verify counterfactually**

Run: `uv run pytest -q tests/arena/test_benchmark_deploy.py tests/test_game_launcher.py tests/test_launcher_cli.py`

Temporarily map `UNKNOWN` to an Escape send and confirm the unknown-screen test fails; revert and rerun.

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/civ_mcp/arena/benchmark_deploy.py src/civ_mcp/game_launcher.py src/civ_mcp/launcher_cli.py tools/windows/civ6_launcher_bootstrap.py tests/arena/test_benchmark_deploy.py tests/test_game_launcher.py tests/test_launcher_cli.py
git commit -m "fix(launcher): gate boot health and continuation on positive evidence"
```

---

### Task 7: Collect fresh checkout, tuner-owner, and GPU isolation evidence

**Files:**

- Create: `src/civ_mcp/arena/benchmark_live_evidence.py`
- Create: `tests/arena/test_benchmark_live_evidence.py`
- Modify: `src/civ_mcp/arena/benchmark_gates.py`
- Modify: `tests/arena/test_benchmark_gates.py`

**Interfaces:**

- Consumes: Task 6 boot evidence, vendored endpoint registry, local/Windows Git checkouts, Linux socket/process metadata, remote `nvidia-smi`/cgroup evidence, and existing pure checkout/GPU gates.
- Produces: `CommandResult`, `TunerHolder`, `GpuProcess`, checkout/tuner/GPU collector functions, and exact PID/service-scoped remediation functions returning JSON-safe evidence.

- [ ] **Step 1: Add failing pure-parser and safety tests**

Cover WSL/Windows checkout evidence, FireTuner ownership, and remote GPU snapshots:

```python
def test_checkout_evidence_records_nonempty_commit_and_porcelain_status(): ...
def test_unknown_tuner_holder_always_blocks(): ...
def test_targeted_tuner_termination_revalidates_pid_start_cmdline_and_cwd(): ...
def test_no_tuner_holder_does_not_issue_a_kill(): ...
def test_gpu_snapshot_maps_uuid_to_index_and_pid_to_service(): ...
def test_unknown_gpu_process_always_blocks_even_when_drain_requested(): ...
def test_named_service_drain_rechecks_and_fails_if_process_remains(): ...
```

Use captured command output fixtures. Tests must assert no broad `pkill`, wildcard, or process-name termination command is generated.

- [ ] **Step 2: Run the tests and observe the missing collector module**

Run: `uv run pytest -q tests/arena/test_benchmark_live_evidence.py tests/arena/test_benchmark_gates.py`

Expected: import/API failures.

- [ ] **Step 3: Implement injected command runners and JSON-safe evidence**

Define:

```python
@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

@dataclass(frozen=True)
class TunerHolder:
    pid: int
    start_ticks: int
    cmdline: str
    cwd: str
    known_repo_owned: bool

@dataclass(frozen=True)
class GpuProcess:
    host: str
    gpu_index: int
    gpu_uuid: str
    pid: int
    process_name: str
    service: str | None
```

All external calls go through injected `run_local`, `run_windows`, or `run_ssh` functions. Parse `git rev-parse HEAD` plus `git status --porcelain=v1` on WSL and the native Windows checkout. Empty/missing revisions fail in `check_clean_checkout`.

- [ ] **Step 4: Implement exact FireTuner-holder classification and scoped remediation**

Read socket/PID data, then `/proc/<pid>/stat`, `/proc/<pid>/cmdline`, and `/proc/<pid>/cwd`. A known owner must have an expected civ-mcp executable and a cwd under one of the two exact repo checkouts. If `--terminate-tuner-pid N` is requested, compare all four identity fields to the immediately preceding evidence, send `SIGTERM` only to that PID, wait a bounded interval, and re-run the port-holder check. If it survives or identity changes, block.

- [ ] **Step 5: Implement endpoint-scoped GPU evidence and named service drain**

Resolve the endpoint through the vendored registry, query its host for GPU index/UUID and compute-process PID/name, and map each PID to a service from its cgroup. `check_gpu_conflicts` receives the actual relevant processes, not all GPUs on both hosts. A requested `--drain-gpu-service UNIT` is legal only when `UNIT` exactly matches a registry-managed/cgroup-observed service; stop that single unit with a non-interactive remote command, then re-snapshot. Unknown processes or remaining conflicts block.

- [ ] **Step 6: Verify pure tests and counterfactual safety**

Run: `uv run pytest -q tests/arena/test_benchmark_live_evidence.py tests/arena/test_benchmark_gates.py`

Temporarily drop the start-ticks comparison and verify the PID-reuse test fails; restore and rerun.

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/civ_mcp/arena/benchmark_live_evidence.py src/civ_mcp/arena/benchmark_gates.py tests/arena/test_benchmark_live_evidence.py tests/arena/test_benchmark_gates.py
git commit -m "feat(benchmark): collect safe live admission evidence"
```

---

### Task 8: Wire integrated admission immediately before each counted block

**Files:**

- Create: `src/civ_mcp/arena/benchmark_admission.py`
- Create: `tests/arena/test_benchmark_admission.py`
- Modify: `src/civ_mcp/arena/benchmark_runner.py`
- Modify: `tests/arena/test_benchmark_runner.py`
- Modify: `src/civ_mcp/arena/benchmark_gates.py`
- Modify: `tests/arena/test_benchmark_gates.py`

**Interfaces:**

- Consumes: Tasks 3–7 campaign store, resolved-block handoff, model/tool probes, boot/deploy evidence, live collectors, and session-lock builder.
- Produces: `AdmissionDependencies`, `AdmissionPipeline.admit(...)`, counted/validation/admit-only/one-block CLI modes, numbered admission attempts, and fresh resume validation against immutable session locks.

- [ ] **Step 1: Add failing orchestration tests for exact gate order and freshness**

Use injected async dependencies to record calls:

```python
async def test_admission_runs_all_gates_in_locked_order_then_mints_session(): ...
async def test_admission_failure_never_creates_session_or_runs_trials(): ...
async def test_admit_only_never_mints_reusable_session(): ...
async def test_noncounting_validation_never_mints_counted_fingerprint_pair(): ...
async def test_second_model_gets_fresh_checkout_gpu_endpoint_and_canary_evidence(): ...
async def test_resume_reuses_campaign_lock_but_reacquires_block_admission(): ...
async def test_resume_reuses_existing_session_lock_when_locked_identity_is_unchanged(): ...
async def test_resume_blocks_when_topology_model_or_config_differs_from_session_lock(): ...
async def test_one_block_mode_stops_after_next_manifest_order_block(): ...
```

The expected order is: clean checkout; boot health; tuner holder; save deploy; production reload; popup hygiene; canonical state; GPU isolation; endpoint/model identity; both tool canaries per arm; seed/latency; treatment-can-fire; session lock creation; `run_resolved_block`.

- [ ] **Step 2: Run the focused tests and confirm counted mode still refuses**

Run: `uv run pytest -q tests/arena/test_benchmark_admission.py tests/arena/test_benchmark_runner.py tests/arena/test_benchmark_gates.py`

Expected: new module is absent and non-smoke CLI still reports unwired gates.

- [ ] **Step 3: Implement one injected `AdmissionPipeline`**

Define:

```python
@dataclass(frozen=True)
class AdmissionDependencies:
    checkout_evidence: Callable[..., dict]
    boot_health: Callable[..., dict]
    tuner_evidence: Callable[..., dict]
    deploy_save: Callable[..., dict]
    reload_and_capture: Callable[..., Awaitable[dict]]
    gpu_evidence: Callable[..., dict]
    resolve_endpoint: Callable[..., dict]
    probe_backend: Callable[..., Awaitable[BackendProbe]]
    probe_tool_capability: Callable[..., Awaitable[ToolCanaryEvidence]]

class AdmissionPipeline:
    async def admit(self, campaign, block, store, *, mode: str) -> ResolvedBlock | dict: ...
```

The pipeline journals each gate result in the campaign journal and writes the complete diagnostic to the next `admissions/<block-id>-attempt-NNN.json`. On the first `mode="counted"` admission for a block, all-green evidence calls `build_session_lock` and returns a `ResolvedBlock`. On resume, the same gates run again, but immutable `session.json` is reused only when model, endpoint, topology, sampling, schema, and code/position identities match. New volatile latency/health evidence remains in the numbered admission attempt. A changed locked identity blocks resume instead of reminting a lock over existing trials.

- [ ] **Step 4: Wire counted, diagnostic, validation, and smoke CLI modes**

Extend `civ-arena-benchmark`:

```text
--campaign benchmarks/campaigns/<name>.yaml
--run-id <campaign-id>
--admit-only <block-id>
--non-counting-validation <block-id>
--terminate-tuner-pid <exact-pid>
--drain-gpu-service <exact-unit>   # repeatable
--one-block                       # execute only the next incomplete block
--ungated-smoke                    # existing suite path, never counted
```

Make `--campaign` and `--suite --ungated-smoke` mutually exclusive. The default campaign path runs blocks in manifest order, reacquiring admission immediately before each incomplete block. `--one-block` exits after that next block and cannot select or reorder it. `--admit-only` exits after diagnostics. Validation writes under `benchmark_runs/<id>/validation/` with a validation stamp and no counted fingerprint pair.

- [ ] **Step 5: Keep the Gemma/Qwen admission semantics explicit**

Gemma must complete a counted block. Qwen is mandatory-to-attempt. Only after Gemma passes may repeated Qwen admission failures be recorded as `REPLICATION_DEFERRED_ADMISSION`; they are not a model null and do not create trials. That final campaign disposition is implemented in Task 11, but the admission artifact must expose the typed failure and all remediation attempts.

- [ ] **Step 6: Verify with counterfactual ordering and freshness tests**

Run: `uv run pytest -q tests/arena/test_benchmark_admission.py tests/arena/test_benchmark_runner.py tests/arena/test_benchmark_gates.py`

Temporarily move session creation before the tool canary and prove the ordering test fails; restore and rerun.

Expected: all pass, and the old “gates not wired” refusal test is replaced by a test that refuses a counted run on the first failed live gate.

- [ ] **Step 7: Commit**

```bash
git add src/civ_mcp/arena/benchmark_admission.py src/civ_mcp/arena/benchmark_runner.py src/civ_mcp/arena/benchmark_gates.py tests/arena/test_benchmark_admission.py tests/arena/test_benchmark_runner.py tests/arena/test_benchmark_gates.py
git commit -m "feat(benchmark): gate counted model blocks at execution time"
```

---

### Task 9: Add safe entity predicates and the production-path position CLI

**Files:**

- Modify: `src/civ_mcp/arena/benchmark_manifest.py`
- Modify: `tests/arena/test_benchmark_manifest.py`
- Modify: `benchmarks/positions/smoke-seondeok-pyramid-v1.yaml`
- Modify: `src/civ_mcp/arena/action_metrics.py`
- Modify: `tests/arena/test_action_metrics.py`
- Modify: `src/civ_mcp/arena/benchmark_runner.py`
- Modify: `tests/arena/test_benchmark_runner.py`
- Create: `src/civ_mcp/arena/benchmark_position.py`
- Create: `tests/arena/test_benchmark_position.py`
- Modify: `pyproject.toml`

**Interfaces:**

- Consumes: Task 8 deploy/reload/popup/canonical-state production path, existing predicate evaluator, and strict position manifests.
- Produces: `validate_position_contract(position) -> None`, safe `unit_exists_final`/`unit_at`/`tile_state_equals` predicates, top-level `reload_position(connection, position) -> bool`, and `civ-arena-benchmark-position capture|verify`.

- [ ] **Step 1: Write failing lifecycle and predicate tests**

Add required `persistent_unit_ids` and `consumable_unit_ids` fields to position manifests and test:

```python
def test_manifest_rejects_unit_declared_persistent_and_consumable(): ...
def test_unit_at_returns_false_when_persistent_unit_disappears_at_runtime(): ...
def test_unit_at_raises_when_unit_is_missing_from_initial_state(): ...
def test_distance_predicate_returns_false_when_persistent_unit_disappears(): ...
def test_authoring_validation_rejects_distance_predicate_for_consumable_unit(): ...
def test_consumable_unit_is_scored_by_resulting_tile_state_not_distance(): ...
def test_tile_state_equals_finds_tile_by_coordinates_not_list_offset(): ...
```

Update the smoke manifest with explicit lists based on its rubric (both empty are valid because it has no entity predicate).

- [ ] **Step 2: Write failing CLI tests that prove production-function reuse**

Use spies on the exact imported callables:

```python
async def test_capture_deploys_then_calls_runner_reload_then_capture_state(): ...
async def test_verify_redeploys_on_all_twelve_cycles(): ...
async def test_verify_aborts_on_first_digest_mismatch(): ...
async def test_verify_calls_shared_popup_hygiene_before_each_capture(): ...
def test_position_cli_requires_exactly_twelve_cycles_for_freeze_mode(): ...
```

Assert call order for every cycle: `deploy_via_windows` → production `reload_position` → `dismiss_blocking_popups` → `capture_canonical_state` → digest compare.

- [ ] **Step 3: Run focused tests and observe absent lifecycle/CLI behavior**

Run: `uv run pytest -q tests/arena/test_benchmark_manifest.py tests/arena/test_action_metrics.py tests/arena/test_benchmark_runner.py tests/arena/test_benchmark_position.py`

Expected: new tests fail.

- [ ] **Step 4: Implement the safe predicate vocabulary and authoring validator**

Add `unit_exists_final`, `unit_at`, and `tile_state_equals` predicates. Unit predicates require the unit in canonical initial state; final disappearance returns false. `tile_state_equals` locates the declared relevant tile by `(x, y)` and compares a named field, avoiding list-index coupling. Change `unit_distance_decreased` to the same safe runtime behavior, then validate it only for IDs in `persistent_unit_ids`. IDs in `consumable_unit_ids` must be scored through a tile/state predicate. Reject undeclared unit IDs, overlap, and lifecycle declarations for IDs absent from canonical state.

Expose:

```python
def validate_position_contract(position: PositionManifest) -> None: ...
```

Call it in manifest admission and the position-freeze CLI.

- [ ] **Step 5: Extract the existing reload closure as the single production function**

Move the body of `_build_live_dependencies`' nested reload closure to:

```python
async def reload_position(connection: GameConnection, position: PositionManifest) -> bool: ...
```

The dependencies closure calls this function directly. Keep `_reload_result_is_success` and its verified/unverified semantics unchanged. The new CLI imports this function; it does not duplicate the classifier.

- [ ] **Step 6: Implement `civ-arena-benchmark-position`**

Add the console script and two subcommands:

```text
civ-arena-benchmark-position capture \
  --authoring-provenance benchmarks/provenance/builder-economy-cal-v1-authoring.json \
  --output benchmarks/provenance/builder-economy-cal-v1-capture.json

civ-arena-benchmark-position verify \
  --position benchmarks/positions/builder-economy-cal-v1.yaml \
  --cycles 12 --output benchmarks/provenance/builder-economy-cal-v1-reloads.json
```

The authoring-provenance input contains the archive path/digest, game save name, player ID, relevant tile coordinates, game build, ruleset, DLC, mods, base-save identity, and mutation journal. `capture` validates those fields and writes the post-reload normalized state/digest plus deployment/reload evidence. `verify` freshly deploys on every cycle, runs the same production reload/capture path, stops at the first mismatch, and writes all twelve successful digests only when complete. Neither command advances a turn.

- [ ] **Step 7: Verify with counterfactual production-path tests**

Run: `uv run pytest -q tests/arena/test_benchmark_manifest.py tests/arena/test_action_metrics.py tests/arena/test_benchmark_runner.py tests/arena/test_benchmark_position.py`

Temporarily replace the production reload call with a local stub and prove the spy test fails; restore and rerun.

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/civ_mcp/arena/benchmark_manifest.py src/civ_mcp/arena/action_metrics.py src/civ_mcp/arena/benchmark_runner.py src/civ_mcp/arena/benchmark_position.py tests/arena/test_benchmark_manifest.py tests/arena/test_action_metrics.py tests/arena/test_benchmark_runner.py tests/arena/test_benchmark_position.py benchmarks/positions/smoke-seondeok-pyramid-v1.yaml
git commit -m "feat(benchmark): freeze positions through the production reload path"
```

---

### Task 10: Implement deterministic campaign reporting and verdict attribution

**Files:**

- Create: `src/civ_mcp/arena/benchmark_campaign_report.py`
- Create: `tests/arena/test_benchmark_campaign_report.py`
- Modify: `src/civ_mcp/arena/benchmark_report.py`
- Modify: `tests/arena/test_benchmark_report.py`
- Modify: `pyproject.toml`

**Interfaces:**

- Consumes: Tasks 2–4 contract/campaign/block locks and dual stamps, existing `score_trial`/per-block reports, Task 9 predicate semantics, human `audit.json`, and optional `tie-attribution.json`.
- Produces: `build_campaign_report`, `render_campaign_markdown`, `write_campaign_reports`, deterministic campaign verdicts, and `civ-arena-benchmark-campaign-report`.

- [ ] **Step 1: Write failing report fixtures for every verdict path**

Build tiny campaign directories in tests and cover:

```python
def test_report_keeps_every_model_arm_and_position_separate(): ...
def test_report_refuses_trials_missing_either_fingerprint(): ...
def test_report_refuses_incomplete_schedule_without_valid_qwen_deferral(): ...
def test_sensitivity_requires_ten_decided_pairs(): ...
def test_direction_requires_ten_standard_wins_out_of_original_twelve(): ...
def test_effect_requires_frozen_normalized_threshold(): ...
def test_zero_ties_are_only_floor_candidates_until_reviewed(): ...
def test_nonzero_ties_are_only_rubric_candidates_until_reviewed(): ...
def test_one_rubric_caused_tie_makes_block_nondiscriminative(): ...
def test_model_floor_and_same_progress_null_preserve_other_block(): ...
def test_sufficiently_decided_separation_failure_is_model_null(): ...
def test_metric_fidelity_disagreement_blocks_campaign_verdict(): ...
def test_gemma_pass_with_valid_qwen_deferral_is_calibrated_deferred(): ...
def test_report_never_reads_attempts_directory(): ...
def test_report_regenerates_byte_identically_without_wall_clock_fields(): ...
def test_scorer_only_fingerprint_change_rescores_same_raw_trials(): ...
```

Include the four aggregate outcomes: `CALIBRATED`, `CALIBRATED_REPLICATION_DEFERRED`, `BLOCKED`, and `RUBRIC_NONDISCRIMINATIVE`, plus per-block `MODEL_FLOOR_NULL` and `MODEL_TIE_NULL`.

- [ ] **Step 2: Add failing audit-fidelity and attribution schema tests**

Define immutable post-trial review files:

```json
{
  "session_fingerprint": "...",
  "audit_indices": [1, 2, 11, 12, 23, 24],
  "trials": [{
    "index": 1,
    "trial_sha256": "...",
    "automatic": {"task_scores": {}, "useful_actions": 0, "domain_rejections": 0, "repetitions": 0},
    "manual": {"task_scores": {}, "useful_actions": 0, "domain_rejections": 0, "repetitions": 0},
    "agrees": true,
    "notes": "..."
  }]
}
```

Tie attribution files cite every tied trial pair, both raw trial digests, transcript/final-state findings, counterfactual fixture result, and one allowed attribution. Missing audits, mismatches, missing tied pairs, or changed trial hashes block an official verdict.

- [ ] **Step 3: Run the focused tests and observe the absent campaign reporter**

Run: `uv run pytest -q tests/arena/test_benchmark_report.py tests/arena/test_benchmark_campaign_report.py`

Expected: imports and verdict assertions fail.

- [ ] **Step 4: Implement a pure campaign projection**

Add:

```python
def build_campaign_report(campaign_dir: str | Path) -> dict[str, object]: ...
def render_campaign_markdown(report: Mapping[str, object]) -> str: ...
def write_campaign_reports(campaign_dir: str | Path) -> dict[str, object]: ...
```

For each block, call the existing per-block scorer, normalize each trial by that frozen rubric maximum, pair by `pair_id`, and retain all twelve signed deltas. Load only locks, schedules, audits/attributions, and `trials/`. Admission/attempt counts may be summarized from immutable admission/journal metadata but are never passed to scoring.

- [ ] **Step 5: Encode the locked sensitivity/separation arithmetic**

For each completed block:

```python
decided = sum(delta != 0 for delta in deltas)
standard_wins = sum(delta > 0 for delta in deltas)
median_delta = statistics.median(deltas)
```

Require `decided >= 10`, `standard_wins >= 10`, and `median_delta >= rules.minimum_median_normalized_delta`. If fewer than ten are decided, require reviewed tie attribution before a block/campaign verdict. Mechanical zero/nonzero labels never decide attribution themselves.

- [ ] **Step 6: Keep output deterministic and fingerprinted**

Use canonical JSON plus stable Markdown ordering. Include both evidence fingerprints, all contract versions, scorer fingerprint, model configuration, endpoint/GPU topology, trial/audit hashes, and report inputs. Do not call `datetime.now()` or read filesystem mtimes. Add `civ-arena-benchmark-campaign-report` to `pyproject.toml`.

- [ ] **Step 7: Prove the scorer and reporter tests counterfactually**

Run: `uv run pytest -q tests/arena/test_benchmark_report.py tests/arena/test_benchmark_campaign_report.py`

Then temporarily pool arms in one grouping and confirm the separation test fails; restore and rerun. Generate both reports twice and compare SHA-256 hashes.

Expected: all pass and hashes match.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/civ_mcp/arena/benchmark_campaign_report.py src/civ_mcp/arena/benchmark_report.py tests/arena/test_benchmark_campaign_report.py tests/arena/test_benchmark_report.py
git commit -m "feat(benchmark): derive calibrated campaign verdicts"
```

---

### Task 11: Construct and freeze `BUILDER_ECONOMY_CAL_V1` in the live game

**Required skill:** Use `civ6-arena-live` for all game/FireTuner operation in this task.

**Files:**

- Create: `benchmarks/saves/BUILDER_ECONOMY_CAL_V1.Civ6Save`
- Create: `benchmarks/positions/builder-economy-cal-v1.yaml`
- Create: `benchmarks/provenance/builder-economy-cal-v1-authoring.json`
- Create: `benchmarks/provenance/builder-economy-cal-v1-reloads.json`
- Modify: `tests/arena/test_benchmark_manifest.py`
- Modify: `tests/arena/test_benchmark_gates.py`

**Interfaces:**

- Consumes: Task 9 position CLI/validators and the live `civ6-arena-live` operating procedure.
- Produces: the archived save, canonical position manifest, environment/mutation provenance, post-reload capture, and twelve-cycle reload evidence consumed by campaign locking/admission.

- [ ] **Step 1: Establish a green pre-live checkpoint**

Run:

```bash
uv run pytest -q tests/arena/test_benchmark_*.py tests/arena/test_action_metrics.py tests/test_game_launcher.py tests/test_launcher_cli.py
git status --short
```

Expected: all tests pass and only intentional plan-implementation changes are present. Do not start authoring from an uncommitted implementation state.

- [ ] **Step 2: Preflight the game and capture environment identity**

Use the live skill to verify: native Windows launcher checkout at the same commit; boot-health green; tuner port unowned before connection; stable real display; no modal popup. Load a stable organic base save and record, before mutation:

- base save name and archive SHA-256;
- game executable/build version;
- ruleset;
- enabled DLC list;
- enabled mod list and versions;
- active player/civilization, turn, map seed, and game seed.

Write these as canonical JSON fields in `builder-economy-cal-v1-authoring.json`. If any identity cannot be queried, stop this task rather than writing `unknown` into the provenance.

- [ ] **Step 3: Create exactly three isolated builder tasks with minimal mutations**

Through FireTuner, make only the changes necessary to produce:

1. one builder adjacent to a pillaged owned resource improvement;
2. one builder adjacent to an owned, unimproved luxury or strategic resource on a directly improvable flat/roaded tile;
3. one builder adjacent to an owned flat/roaded resource tile whose feature must be removed before improvement.

Give each builder at least two charges so intended success does not consume it. Remove nearby military threats, empty production/research blockers, and irrelevant idle units only when they would contaminate the single-turn decision. Do not change unrelated cities, yields, diplomacy, or map areas. Journal every executed mutation with before/after values and the exact unit/tile IDs.

- [ ] **Step 4: Prove both arms can reach their intended rubric levels**

Query through the benchmark-safe tool surfaces, not an unrestricted debug view:

- minimal can call `get_units`, `get_cities`, and `move_unit`, observe all three builders/tasks sufficiently to identify them, and move each builder onto its target (levels 1–2);
- standard exposes the same base tools plus `get_builder_tasks`, `repair_improvement`, `improve_tile`, and `remove_feature` and can legally perform each intended mutation (level 4);
- neither arm exposes `end_turn`; both expose `finish_trial`.

Use test/probe calls that do not mutate the would-be frozen state, or reload the authoring state before saving if a legality probe mutates it.

- [ ] **Step 5: Save, archive, deploy, reload, then capture canonical state**

Save as exactly `BUILDER_ECONOMY_CAL_V1`. Copy the resulting Windows save into `benchmarks/saves/`, compute SHA-256, and append the archive/source/deployment evidence to the provenance journal.

Run the production-path capture command from Task 9. The live authoring-session state is diagnostic only; the `expected_state` and digest must come from this post-archive deploy/reload:

```bash
uv run civ-arena-benchmark-position capture \
  --authoring-provenance benchmarks/provenance/builder-economy-cal-v1-authoring.json \
  --output benchmarks/provenance/builder-economy-cal-v1-capture.json
```

The provenance file already contains the actual three recorded coordinates; no guessed, shell-substituted, or provisional coordinate may enter the manifest.

- [ ] **Step 6: Author the position manifest and frozen 0/1/2/4 rubric**

Use the capture's exact state/digest, archive digest, three builder IDs, and target coordinates. Declare all three builders persistent. For each equal-weight task:

- score 0 is implicit when no level matches;
- score 1 requires actual observation through minimal-available state tools;
- score 2 requires the correct builder to survive and occupy its intended target;
- score 4 requires the exact tile-state mutation (pillaged false, intended improvement present, or blocking feature absent).

Do not add level 3 unless live state contains a separate observable intermediate. Use coordinate-based tile predicates, not brittle list indices. Add tests that load the real manifest, validate all lifecycle/predicate references, prove minimal can reach levels 1–2, and standard can reach level 4 in counterfactual state fixtures.

- [ ] **Step 7: Run all twelve fresh deployment/reload checks through the production path**

Run:

```bash
uv run civ-arena-benchmark-position verify \
  --position benchmarks/positions/builder-economy-cal-v1.yaml \
  --cycles 12 \
  --output benchmarks/provenance/builder-economy-cal-v1-reloads.json
```

Expected: twelve records, each with a fresh deployment hash match, verified production reload, popup hygiene success, and the exact frozen state digest. A single mismatch invalidates the freeze; diagnose and repeat the complete twelve-cycle check after fixing the artifact.

- [ ] **Step 8: Verify repository artifacts and commit**

Run:

```bash
sha256sum benchmarks/saves/BUILDER_ECONOMY_CAL_V1.Civ6Save
uv run pytest -q tests/arena/test_benchmark_manifest.py tests/arena/test_benchmark_gates.py tests/arena/test_benchmark_position.py tests/arena/test_action_metrics.py
git diff --check
```

Expected: archive hash matches the manifest/provenance, focused tests pass, no whitespace errors.

Commit:

```bash
git add benchmarks/saves/BUILDER_ECONOMY_CAL_V1.Civ6Save benchmarks/positions/builder-economy-cal-v1.yaml benchmarks/provenance/builder-economy-cal-v1-authoring.json benchmarks/provenance/builder-economy-cal-v1-capture.json benchmarks/provenance/builder-economy-cal-v1-reloads.json tests/arena/test_benchmark_manifest.py tests/arena/test_benchmark_gates.py
git commit -m "test(benchmark): freeze builder economy calibration position"
```

---

### Task 12: Freeze the counted campaign and pass non-counting validation

**Required skill:** Use `civ6-arena-live` for the validation episodes.

**Files:**

- Create: `benchmarks/campaigns/builder-economy-cal-v1.yaml`
- Create: `benchmarks/contracts/instrument-v1-candidate.yaml`
- Modify: `tests/arena/test_benchmark_contract.py`
- Modify: `tests/arena/test_benchmark_schedule.py`
- Evidence: `benchmark_runs/builder-economy-cal-v1/validation/`

**Interfaces:**

- Consumes: Tasks 2, 9, 10 contract freezer, frozen position/rubric, campaign scheduler/reporter, and Task 8 non-counting validation mode.
- Produces: the committed campaign manifest/config commit, candidate instrument contract, runtime `campaign.json`, and structurally non-counting validation evidence.

- [ ] **Step 1: Write the release-candidate contract and campaign manifest**

First generate the candidate contract from the exact scorer source that now exists:

```bash
uv run python -m civ_mcp.arena.benchmark_contract freeze \
  --evidence-version 1.0.0 \
  --predicate-version 1.0.0 \
  --report-version 1.0.0 \
  --output benchmarks/contracts/instrument-v1-candidate.yaml
```

Then freeze this complete campaign before viewing either model's counted transcript:

```yaml
campaign_id: builder-economy-cal-v1
campaign_schema_version: 1.0.0
position: builder-economy-cal-v1
position_provenance: ../provenance/builder-economy-cal-v1-authoring.json
contracts: ../contracts/instrument-v1-candidate.yaml
prompt: >-
  Assess the current game situation, issue the best available orders for this
  turn, and call finish_trial when you are done.
models:
  - block_id: gemma4-26b
    model: gemma4-26b
    endpoint_id: home-gpu0-cpp
    sampling: {temperature: 0.2, top_p: 0.95, seed: 101, max_tokens: 3072}
    chat_template_kwargs: {enable_thinking: false}
    briefing_required: false
  - block_id: qwen3.6-27b
    model: qwen3.6-27b
    endpoint_id: home-gpu0-cpp
    sampling: {temperature: 0.2, top_p: 0.95, seed: 101, max_tokens: 6144}
    chat_template_kwargs: {enable_thinking: false}
    briefing_required: false
arms:
  - {arm_id: minimal, tools: minimal, options: {}}
  - {arm_id: standard, tools: standard, options: {}}
seeds: [101, 211, 307, 401, 503, 601, 701, 809, 907, 1009, 1103, 1201]
order: abba
driver: single_turn
fresh_conversation_per_trial: true
retry_policy: {max_attempts: 1, backoff_s: 0.0}
max_steps: 8
result_char_cap: 4000
audit_indices: [1, 2, 11, 12, 23, 24]
rules:
  pairs_per_model: 12
  minimum_decided_pairs: 10
  minimum_standard_wins: 10
  minimum_median_normalized_delta: 0.3333333333333333
  required_audits_per_arm: 3
```

`home-gpu0-cpp` is the exact vendored-registry endpoint on `home-llm`; the live identity/isolation gate must prove it serves each requested model before that block runs. If the endpoint cannot serve one model, revise and recommit the campaign before validation rather than substituting a floating “local” alias at runtime. The candidate contract pins the three schema versions, prompt/rubric/tool identities, scorer source fingerprint, predicate rules, audit schema, tie-attribution schema, and 4/12 effect-threshold derivation.

- [ ] **Step 2: Add tests pinning every preregistered choice**

Assert: Gemma first/Qwen second; 12 exact seeds; 24 local trials per block; ABBA arm order; audits resolve to 3 minimal/3 standard across early/middle/late; identical arms except tool tier; treatment options empty; prompt contains no position name, coordinates, IDs, resource/builder/rubric language; no briefing/tracker/playbook/memory/channels/attention; no `end_turn`; both arms contain `finish_trial`; threshold equals 4 divided by the frozen rubric maximum.

- [ ] **Step 3: Run static gates and commit the freeze before validation**

Run:

```bash
uv run pytest -q tests/arena/test_benchmark_contract.py tests/arena/test_benchmark_schedule.py tests/arena/test_benchmark_manifest.py tests/arena/test_benchmark_campaign_report.py
git diff --check
```

Expected: all pass.

Commit:

```bash
git add benchmarks/campaigns/builder-economy-cal-v1.yaml benchmarks/contracts/instrument-v1-candidate.yaml tests/arena/test_benchmark_contract.py tests/arena/test_benchmark_schedule.py
git commit -m "test(benchmark): preregister builder calibration campaign"
```

- [ ] **Step 4: Run one non-counting episode per arm through full live admission**

Fast-forward the native Windows checkout to the just-created campaign/config freeze commit and confirm it is clean. Then run:

```bash
uv run civ-arena-benchmark \
  --campaign benchmarks/campaigns/builder-economy-cal-v1.yaml \
  --run-id builder-economy-cal-v1 \
  --wsl-repo "$(git rev-parse --show-toplevel)" \
  --windows-repo /mnt/c/Users/wrisl/dev/civ6-mcp \
  --non-counting-validation gemma4-26b
```

(Both repo flags carry derived defaults — the running checkout's git root and the Windows companion checkout the launcher bootstrap already uses — so they are optional; they are spelled out here so the runbook is explicit about which checkouts the clean-checkout/tuner-holder gates compare.)

Expected: full fresh gate evidence, one minimal and one standard episode, no campaign/session fingerprint pair on validation trials, no writes under counted `blocks/`, and deterministic validation reporting.

- [ ] **Step 5: Inspect validation only for instrument defects**

Confirm: both tool canaries pass; the starting digest is exact; popup hygiene succeeds; both arms can finish; minimal can reach rubric levels 1–2; standard's three intended actions are legal; automated metrics match the two transcripts; the validation report regenerates byte-identically; counted report refuses the validation evidence.

If the prompt/rubric/predicate/report semantics need revision, increment the relevant contract/campaign version, delete no evidence, create a new campaign run directory, commit the new freeze, and repeat Steps 4–5. Do not begin Task 13 until validation is green under the exact frozen campaign that will count.

- [ ] **Step 6: Seal the validation disposition without changing the campaign commit**

Add a deterministic validation summary under the validation directory containing artifact hashes and pass/fail checks; it is explicitly marked non-counting. `benchmark_runs/` is gitignored, so retain this evidence in place but do not force-add or commit it yet. The commit pinned by `campaign.json` must remain unchanged through both counted blocks.

```bash
find benchmark_runs/builder-economy-cal-v1/validation -type f ! -name SHA256SUMS -print0 \
  | sort -z \
  | xargs -0 sha256sum \
  > benchmark_runs/builder-economy-cal-v1/validation/SHA256SUMS
git status --porcelain=v1
```

Expected: the hash ledger is complete and Git reports a clean tracked tree.

Run the full repository suite once more after validation and before any counted trial:

```bash
uv run pytest -q
```

Expected: all tests pass on the exact commit pinned by `campaign.json`.

---

### Task 13: Run and audit the mandatory Gemma4 counted block

**Required skill:** Use `civ6-arena-live`; keep the display/AVR path active and avoid mouse/keyboard interference during reload recognition.

**Files:**

- Evidence: `benchmark_runs/builder-economy-cal-v1/campaign.json`
- Evidence: `benchmark_runs/builder-economy-cal-v1/schedule.json`
- Evidence: `benchmark_runs/builder-economy-cal-v1/admissions/gemma4-26b-attempt-NNN.json`
- Evidence: `benchmark_runs/builder-economy-cal-v1/blocks/gemma4-26b/`

**Interfaces:**

- Consumes: Task 12 frozen campaign commit/runtime lock and Task 8 integrated admission/one-block execution.
- Produces: one complete immutable Gemma session, 24 dual-stamped trials, admission/attempt evidence, `audit.json`, optional `tie-attribution.json`, and a sealed block hash ledger.

- [ ] **Step 1: Start from the committed clean tree and run fresh admission**

Run `git status --porcelain=v1` in WSL and Windows; both must be empty and both commits identical. Run the campaign without any stale `--admit-only` authorization:

```bash
uv run civ-arena-benchmark \
  --campaign benchmarks/campaigns/builder-economy-cal-v1.yaml \
  --run-id builder-economy-cal-v1 \
  --wsl-repo "$(git rev-parse --show-toplevel)" \
  --windows-repo /mnt/c/Users/wrisl/dev/civ6-mcp \
  --one-block
```

The campaign begins Gemma first. If a known repo tuner holder or named managed GPU service blocks, use only the exact remediation flag after reviewing its evidence. Unknown owners are hard blocks.

- [ ] **Step 2: Let all 24 Gemma trials complete serially**

Expected: 12 minimal/standard pairs, fresh reload checksum before every trial, fresh conversation per trial, scheduled seed sent, no turn advance, no discarded scoreable failures, and every infrastructure retry retained under `attempts/`. A session interruption resumes only after new admission and skips only dual-fingerprint-matching complete trials.

- [ ] **Step 3: Generate the provisional block report and audit six frozen indices**

Run:

```bash
uv run civ-arena-benchmark-report benchmark_runs/builder-economy-cal-v1/blocks/gemma4-26b
```

For local indices `1, 2, 11, 12, 23, 24`, hand-check transcript, state deltas, rubric task scores, useful actions, domain rejections, and repetitions. Write `audit.json` using Task 10's schema and exact trial hashes. Any disagreement is an instrument failure; correct the semantics/version and rerun under a new campaign rather than retaining affected evidence. A sufficiently decided block that misses direction or effect is `MODEL_NULL`, not a rubric rewrite trigger.

- [ ] **Step 4: Resolve ties before assigning a Gemma block result**

If at least 10 pairs are decided, apply the direction/effect gates. If fewer, review every tied pair and write `tie-attribution.json`; mechanical 0–0/nonzero labels are only starting hypotheses. Any rubric-caused consequential tie makes the campaign `RUBRIC_NONDISCRIMINATIVE` and blocks reuse of this evidence after a rubric edit.

- [ ] **Step 5: Seal the immutable Gemma evidence and audits without committing**

Verify no secrets, host tokens, or API keys appear. Write a sorted SHA-256 ledger inside the Gemma block, then confirm the tracked tree still matches the campaign commit. Do not force-add ignored evidence yet; a commit here would invalidate Qwen's checkout gate.

```bash
find benchmark_runs/builder-economy-cal-v1/blocks/gemma4-26b -type f ! -name SHA256SUMS -print0 \
  | sort -z \
  | xargs -0 sha256sum \
  > benchmark_runs/builder-economy-cal-v1/blocks/gemma4-26b/SHA256SUMS
test "$(git rev-parse HEAD)" = "$(python3 -c 'import json; print(json.load(open("benchmark_runs/builder-economy-cal-v1/campaign.json"))["expected_commit"])')"
```

---

### Task 14: Attempt and audit the Qwen3.6 replication block

**Required skill:** Use `civ6-arena-live`; run only after the Gemma block/audit disposition is recorded.

**Files:**

- Evidence: `benchmark_runs/builder-economy-cal-v1/admissions/qwen3.6-27b-attempt-NNN.json`
- Evidence: `benchmark_runs/builder-economy-cal-v1/blocks/qwen3.6-27b/`
- Modify: `benchmark_runs/builder-economy-cal-v1/campaign-journal.jsonl`

**Interfaces:**

- Consumes: Task 13 Gemma disposition, the same campaign lock, and fresh Task 8 Qwen admission.
- Produces: either a complete audited 24-trial Qwen block or the exact append-only evidence required for `REPLICATION_DEFERRED_ADMISSION`.

- [ ] **Step 1: Reacquire every admission fact for Qwen**

Resume the same campaign command. Confirm exact endpoint/model identity, `enable_thinking: false`, `max_tokens: 6144`, both canaries for both arms, exact-sampling seed/latency probe, and latency-derived episode wall in the new block lock. Do not infer Qwen health from Gemma evidence.

- [ ] **Step 2: Handle admission failure without laundering it into a null**

If Qwen admission fails, make one concrete remediation and retry, or make two confirming attempts only when the capability is demonstrated non-remediable. Journal each attempt. `REPLICATION_DEFERRED_ADMISSION` is allowed only when Gemma already passed; it creates no Qwen session/trials and is never a model null. If Gemma did not pass, inability to admit Qwen makes Plan 2 `BLOCKED`.

- [ ] **Step 3: If admitted, run all 24 trials serially and generate the block report**

Run/resume:

```bash
uv run civ-arena-benchmark \
  --campaign benchmarks/campaigns/builder-economy-cal-v1.yaml \
  --run-id builder-economy-cal-v1 \
  --wsl-repo "$(git rev-parse --show-toplevel)" \
  --windows-repo /mnt/c/Users/wrisl/dev/civ6-mcp

uv run civ-arena-benchmark-report benchmark_runs/builder-economy-cal-v1/blocks/qwen3.6-27b
```

Expected: identical position, arms, seed schedule, and prompt; only the locked model configuration differs.

- [ ] **Step 4: Audit the same six local indices and attribute all ties**

Hand-check indices `1, 2, 11, 12, 23, 24`, write hash-bound `audit.json`, and write `tie-attribution.json` when fewer than 10 pairs are decided. A genuine model floor/same-progress null preserves Gemma evidence; rubric nondiscrimination invalidates the instrument campaign.

- [ ] **Step 5: Seal Qwen evidence or its valid admission deferral**

```bash
find benchmark_runs/builder-economy-cal-v1/blocks/qwen3.6-27b -type f ! -name SHA256SUMS -print0 \
  | sort -z \
  | xargs -0 sha256sum \
  > benchmark_runs/builder-economy-cal-v1/blocks/qwen3.6-27b/SHA256SUMS
test "$(git rev-parse HEAD)" = "$(python3 -c 'import json; print(json.load(open("benchmark_runs/builder-economy-cal-v1/campaign.json"))["expected_commit"])')"
```

If Qwen never admitted, omit the nonexistent block hash command and require the numbered failure/remediation admission records plus journal to exist. All campaign evidence is committed together in Task 15.

---

### Task 15: Publish the final campaign report and frozen instrument contract

**Files:**

- Create: `benchmark_runs/builder-economy-cal-v1/report.json`
- Create: `benchmark_runs/builder-economy-cal-v1/report.md`
- Replace: `benchmarks/contracts/instrument-v1-candidate.yaml` with `benchmarks/contracts/instrument-v1.yaml`
- Create: `docs/research/arena-benchmark-builder-calibration-v1-findings.md`
- Modify: `tools/skills/civ6-arena-live/SKILL.md` only if live operation produced a new reusable recovery rule
- Modify: repo-local roadmap/planning docs that still describe the runner or Plan 2 as incomplete

**Interfaces:**

- Consumes: Tasks 10, 13, and 14 deterministic reporting code, both audited blocks or valid Qwen deferral evidence, all frozen contracts, and the unchanged campaign commit identity.
- Produces: final report JSON/Markdown, calibrated or blocked verdict, released `instrument-v1.yaml` only when eligible, research findings, plan/skill synchronization, and pushed evidence.

- [ ] **Step 1: Run the complete automated verification suite before reporting**

Run:

```bash
uv run pytest -q
git diff --check
```

Expected: full suite green. Record the exact pass count in the findings document only after this command completes.

- [ ] **Step 2: Generate and independently regenerate the campaign report**

Run twice:

```bash
uv run civ-arena-benchmark-campaign-report benchmark_runs/builder-economy-cal-v1
sha256sum benchmark_runs/builder-economy-cal-v1/report.json benchmark_runs/builder-economy-cal-v1/report.md
```

Expected: identical hashes on both generations; no generation timestamp; all model/arm groups separate; every schedule/trial/audit/fingerprint validation green; attempts visible operationally but absent from scoring.

- [ ] **Step 3: Apply the preregistered verdict without post-hoc reinterpretation**

Accept `CALIBRATED` only if at least one admitted model passes all three separation gates, every completed block passes metric fidelity, and every tie-heavy block is attributed to model floor/same-progress rather than rubric failure. Accept `CALIBRATED_REPLICATION_DEFERRED` only for a passing Gemma block plus the exact Qwen admission-remediation evidence. Otherwise publish the appropriate blocked/nondiscriminative result; do not modify thresholds or omit unfavorable trials.

- [ ] **Step 4: Release the stable instrument contract only on a calibrated outcome**

For `CALIBRATED` or `CALIBRATED_REPLICATION_DEFERRED`, rename/finalize `instrument-v1.yaml` with the exact evidence, predicate, and report schema versions, scorer fingerprint, predicate vocabulary, authoring conventions, compatibility rules, prompt/rubric fingerprints, and campaign evidence digest. If the outcome is blocked/nondiscriminative, keep the candidate label and document why Plan 3 remains blocked.

- [ ] **Step 5: Write findings and sync operational knowledge**

The findings document states: position provenance; model configs/topology; admissions/retries; full per-model pair results; metric audit; tie review; decision-quality interpretation; limits; exact verdict; and whether Plan 3 (nine-position library and multi-model screen) is unlocked. Run the `skill-evolve` and `repo-plan-sync` skills; update the live skill only for genuinely reusable operational discoveries, not experiment results.

- [ ] **Step 6: Final verification, commit, and push**

Run:

```bash
uv run pytest -q
git status --short
git diff --check
git log --oneline --decorate -12
```

Commit the derived reports, contract/findings, evidence changes, and planning sync:

```bash
git add -f benchmark_runs/builder-economy-cal-v1
git add docs/research/arena-benchmark-builder-calibration-v1-findings.md
# Add tools/skills/civ6-arena-live/SKILL.md and exact roadmap/plan paths only if this task changed them.
git commit -m "docs(benchmark): publish counted builder calibration verdict"
git push origin main
```

For a calibrated outcome, stage `benchmarks/contracts/instrument-v1.yaml`; for a blocked/nondiscriminative outcome, stage `benchmarks/contracts/instrument-v1-candidate.yaml` instead. Never stage both labels as if a candidate had been released.

Verify the remote advanced:

```bash
git fetch origin main
test "$(git rev-parse main)" = "$(git rev-parse origin/main)"
```

Expected: local and remote `main` match. Then fast-forward the native Windows checkout and the `riz-llm` mirror with `git pull --ff-only`, confirming both land on the same commit and remain clean.

---

## Plan 2 exit gate

Plan 2 is complete only when:

- the live admission pipeline is mandatory for counted mode;
- the position archive survives twelve production-path deploy/reload/checksum cycles;
- prompt, rubric, sampling, tool identities, audits, and verdict rules were frozen before counting;
- Gemma produced one complete audited block;
- Qwen produced one complete audited block or the narrowly defined post-Gemma admission deferral;
- reports regenerate byte-identically from locks plus `trials/` and pass metric-fidelity review; and
- the campaign is `CALIBRATED` or `CALIBRATED_REPLICATION_DEFERRED`.

Only then begin Plan 3: the nine-position development/held-out library, non-empty treatment options (briefing/tracker), and the qwen3.8/granite4.2/ornith multi-model screen. If the exit gate is not met, publish the failure honestly and keep Plan 3 blocked.
