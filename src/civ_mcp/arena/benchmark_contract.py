"""Versioned campaign contracts and exact tool identities for the arena
controlled-position calibration campaign.

Plan 2 (see `docs/superpowers/specs/2026-08-31-arena-benchmark-calibration-
campaign-design.md`) freezes one campaign against exactly two model blocks
(Gemma4 then Qwen3.6) before any counted trial runs. This module is the
strict loader for that frozen campaign manifest (`CampaignManifest`) plus the
two distinct tool-schema fingerprints the design calls for:

- `tool_surface_identity` hashes ordered tool *names* and their capability
  IDs only. It proves the minimal/standard treatment differs and belongs in
  the immutable campaign lock (Task 3) -- it is stable across a schema-text
  edit (a description rewrite, say) that changes nothing the model actually
  does with the tool.
- `tool_input_identity` hashes the complete canonical function schema sent to
  the model verbatim (names, descriptions, argument types, required fields).
  Tool schemas are themselves model input, so this is block *admission*
  evidence (Task 4/5's session lock), not campaign identity: changing it
  starts a new counted session without rewriting the scientific campaign.

Every loader here is strict in the same sense as `benchmark_manifest.py`:
missing/extra keys and wrong-typed values raise a field-specific `ValueError`
before any container construction, and the campaign-specific Plan-2
restrictions (exactly two arms, Gemma before Qwen, 12 seeds, ABBA, ...) live
here rather than weakening the reusable `SuiteManifest` rules that other,
future (non-Plan-2) suites will also load through.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Mapping, Sequence

import yaml

from civ_mcp.arena.backends import RetryPolicy, SamplingConfig
from civ_mcp.arena.benchmark_agent import FINISH_TRIAL_TOOL_NAME
from civ_mcp.arena.benchmark_manifest import (
    SuiteManifest,
    TreatmentArm,
    _load_sampling,
    _load_treatment_arm,
    _require_int,
    _require_list,
    _require_mapping,
    _require_str,
    fingerprint,
)
from civ_mcp.arena.benchmark_manifest import _require_keys as _require_exact_keys
from civ_mcp.arena.benchmark_schedule import compile_schedule
from civ_mcp.arena.registry import TOOL_REGISTRY

# Plan-2's preregistered, non-negotiable campaign shape (see the design doc's
# "Frozen campaign configuration" section). A later, non-Plan-2 campaign
# schema version would get its own loader rather than relaxing these.
PLAN2_MODEL_COUNT = 2
PLAN2_ARM_SPEC: tuple[tuple[str, str], ...] = (("minimal", "minimal"), ("standard", "standard"))
# Housekeeping (final review): `_require_plan2_arms` was validating the arm
# count against PLAN2_MODEL_COUNT -- numerically equal (2 models, 2 arms) but
# a different concept; a future Plan revision that changed either count
# independently would silently validate against the wrong one.
PLAN2_ARM_COUNT = len(PLAN2_ARM_SPEC)
PLAN2_SEED_COUNT = 12
PLAN2_ORDER = "abba"
PLAN2_DRIVER = "single_turn"
PLAN2_MAX_STEPS = 8
PLAN2_AUDIT_COUNT = 6

# The exact scorer implementation whose bytes are pinned by
# `ContractVersions.scorer_fingerprint`. Paths are relative to `repo_root`.
SCORER_SOURCE_RELATIVE_PATHS: tuple[Path, ...] = (
    Path("src/civ_mcp/arena/action_metrics.py"),
    Path("src/civ_mcp/arena/benchmark_report.py"),
    Path("src/civ_mcp/arena/benchmark_campaign_report.py"),
)


@dataclasses.dataclass(frozen=True)
class ContractVersions:
    evidence_schema_version: str
    predicate_schema_version: str
    report_schema_version: str
    scorer_fingerprint: str


@dataclasses.dataclass(frozen=True)
class ModelBlockConfig:
    block_id: str
    model: str
    endpoint_id: str
    sampling: SamplingConfig
    chat_template_kwargs: dict[str, object]
    briefing_required: bool


@dataclasses.dataclass(frozen=True)
class CalibrationRules:
    pairs_per_model: int
    minimum_decided_pairs: int
    minimum_standard_wins: int
    minimum_median_normalized_delta: float
    required_audits_per_arm: int


@dataclasses.dataclass(frozen=True)
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


_CAMPAIGN_FIELDS = {f.name for f in dataclasses.fields(CampaignManifest)}
_MODEL_BLOCK_FIELDS = {f.name for f in dataclasses.fields(ModelBlockConfig)}
_CALIBRATION_RULES_FIELDS = {f.name for f in dataclasses.fields(CalibrationRules)}
_CONTRACT_VERSIONS_FIELDS = {f.name for f in dataclasses.fields(ContractVersions)}
_RETRY_POLICY_FIELDS = {f.name for f in dataclasses.fields(RetryPolicy)}


def _require_bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _require_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    return float(value)


# A5 (external review): a block_id is used verbatim as a path segment under
# both `blocks/<block_id>/` and `admissions/<block_id>-attempt-NNN.json` --
# it must be restricted to one safe filename segment so a value like
# `../../escaped` can never traverse outside those directories.
_SAFE_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _require_safe_path_segment(value: str, field: str) -> str:
    if not _SAFE_PATH_SEGMENT_RE.match(value):
        raise ValueError(
            f"{field} must be a single safe filename segment matching "
            f"{_SAFE_PATH_SEGMENT_RE.pattern!r} (got {value!r})"
        )
    if value in (".", "..") or value.startswith("."):
        raise ValueError(f"{field} must not be '.', '..', or start with '.' (got {value!r})")
    return value


def _load_model_block(raw: object, context: str) -> ModelBlockConfig:
    mapping = _require_mapping(raw, context)
    _require_exact_keys(mapping, _MODEL_BLOCK_FIELDS, context)
    briefing_required = _require_bool(mapping["briefing_required"], f"{context}.briefing_required")
    if briefing_required:
        # A10 (external review, "Ruling F"): `ModelBlockConfig` has no
        # budget field by design -- Plan 2 mandates briefing off, so a
        # loaded `briefing_required: true` block would sail past every
        # loader check here and only fail, permanently and silently, deep
        # inside admission's `admit_model_block` call (which pairs
        # `briefing_required` against a budget `AdmissionPipeline` always
        # passes as `None`). Refusing it here, at load time, means that
        # failure can never happen -- and it names the real reason instead
        # of a downstream gate failure.
        raise ValueError(
            f"{context}.briefing_required must be false -- briefing arms are Plan-3 scope, "
            "not supported by this Plan-2 campaign loader"
        )
    return ModelBlockConfig(
        block_id=_require_safe_path_segment(
            _require_str(mapping["block_id"], f"{context}.block_id"), f"{context}.block_id"
        ),
        model=_require_str(mapping["model"], f"{context}.model"),
        endpoint_id=_require_str(mapping["endpoint_id"], f"{context}.endpoint_id"),
        sampling=_load_sampling(mapping["sampling"], f"{context}.sampling"),
        chat_template_kwargs=dict(
            _require_mapping(mapping["chat_template_kwargs"], f"{context}.chat_template_kwargs")
        ),
        briefing_required=briefing_required,
    )


def _load_calibration_rules(raw: object, context: str) -> CalibrationRules:
    mapping = _require_mapping(raw, context)
    _require_exact_keys(mapping, _CALIBRATION_RULES_FIELDS, context)
    return CalibrationRules(
        pairs_per_model=_require_int(mapping["pairs_per_model"], f"{context}.pairs_per_model"),
        minimum_decided_pairs=_require_int(
            mapping["minimum_decided_pairs"], f"{context}.minimum_decided_pairs"
        ),
        minimum_standard_wins=_require_int(
            mapping["minimum_standard_wins"], f"{context}.minimum_standard_wins"
        ),
        minimum_median_normalized_delta=_require_number(
            mapping["minimum_median_normalized_delta"], f"{context}.minimum_median_normalized_delta"
        ),
        required_audits_per_arm=_require_int(
            mapping["required_audits_per_arm"], f"{context}.required_audits_per_arm"
        ),
    )


def _load_retry_policy(raw: object, context: str) -> RetryPolicy:
    mapping = _require_mapping(raw, context)
    _require_exact_keys(mapping, _RETRY_POLICY_FIELDS, context)
    return RetryPolicy(
        max_attempts=_require_int(mapping["max_attempts"], f"{context}.max_attempts", minimum=1),
        backoff_s=_require_number(mapping["backoff_s"], f"{context}.backoff_s"),
    )


def _load_contract_versions_mapping(raw: object, context: str) -> ContractVersions:
    mapping = _require_mapping(raw, context)
    _require_exact_keys(mapping, _CONTRACT_VERSIONS_FIELDS, context)
    return ContractVersions(
        evidence_schema_version=_require_str(
            mapping["evidence_schema_version"], f"{context}.evidence_schema_version"
        ),
        predicate_schema_version=_require_str(
            mapping["predicate_schema_version"], f"{context}.predicate_schema_version"
        ),
        report_schema_version=_require_str(
            mapping["report_schema_version"], f"{context}.report_schema_version"
        ),
        scorer_fingerprint=_require_str(mapping["scorer_fingerprint"], f"{context}.scorer_fingerprint"),
    )


def _load_contract_versions_file(raw_path: str, base_dir: Path) -> ContractVersions:
    resolved = (base_dir / raw_path).resolve()
    if not resolved.is_file():
        raise ValueError(f"campaign manifest.contracts: no such file: {resolved}")
    payload = yaml.safe_load(resolved.read_text())
    return _load_contract_versions_mapping(payload, f"campaign manifest.contracts ({resolved})")


def _resolve_position_provenance(raw_path: str, base_dir: Path) -> str:
    resolved = (base_dir / raw_path).resolve()
    if not resolved.is_file():
        raise ValueError(f"campaign manifest.position_provenance: no such file: {resolved}")
    try:
        payload = json.loads(resolved.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"campaign manifest.position_provenance: not valid JSON ({resolved}): {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError(
            f"campaign manifest.position_provenance: expected a JSON mapping at top level ({resolved})"
        )
    # A digest must actually be derivable from the referenced provenance
    # content -- this is a self-consistency check only. Cross-checking it
    # against a loaded PositionManifest's own archive digest is Task 3's job
    # once both are loaded together to build the campaign lock.
    fingerprint(payload)
    return str(resolved)


def _require_plan2_arms(arms: tuple[TreatmentArm, ...]) -> None:
    if len(arms) != PLAN2_ARM_COUNT:
        raise ValueError(
            f"campaign manifest.arms must declare exactly two Plan 2 arms (minimal, standard); "
            f"got {len(arms)}"
        )
    for arm, (expected_id, expected_tools) in zip(arms, PLAN2_ARM_SPEC):
        if arm.arm_id != expected_id or arm.tools != expected_tools:
            raise ValueError(
                "campaign manifest.arms must be exactly "
                "[minimal(tools=minimal), standard(tools=standard)] in that order for Plan 2; "
                f"got {[(a.arm_id, a.tools) for a in arms]}"
            )
        if arm.options:
            raise ValueError(
                f"campaign manifest.arms[{expected_id!r}].options must be empty for Plan 2; "
                f"got {arm.options!r}"
            )


def load_campaign_manifest(path: str | Path) -> CampaignManifest:
    """Strictly load and Plan-2-validate one campaign manifest YAML file.

    `position_provenance` and `contracts` are declared as paths relative to
    `path`'s own directory; both are resolved here. `contracts` is resolved
    all the way to a loaded `ContractVersions`; `position_provenance` is
    resolved to an absolute path after verifying the referenced file exists,
    parses as a JSON mapping, and is hashable (Task 3 loads its content
    directly to cross-check against the position's own archive digest).
    """
    campaign_path = Path(path)
    raw = yaml.safe_load(campaign_path.read_text())
    if not isinstance(raw, dict):
        raise ValueError("campaign manifest: expected a YAML mapping at top level")
    _require_exact_keys(raw, _CAMPAIGN_FIELDS, "campaign manifest")

    base_dir = campaign_path.parent

    models = tuple(
        _load_model_block(m, f"campaign manifest.models[{i}]")
        for i, m in enumerate(_require_list(raw["models"], "campaign manifest.models"))
    )
    if len(models) != PLAN2_MODEL_COUNT:
        raise ValueError(
            f"campaign manifest.models must declare exactly 2 model blocks for Plan 2 "
            f"(Gemma then Qwen); got {len(models)}"
        )
    # A4 (external review): distinct block_ids are load-bearing, not just
    # cosmetic -- `compile_campaign_schedule` and every on-disk artifact
    # path (`blocks/<block_id>/...`, `admissions/<block_id>-attempt-*.json`)
    # key off block_id alone. A duplicate collapses both model blocks onto
    # one shared run directory, silently merging Gemma's and Qwen's
    # schedules/trials/admissions together.
    block_ids = [model.block_id for model in models]
    if len(set(block_ids)) != len(block_ids):
        duplicates = sorted({block_id for block_id in block_ids if block_ids.count(block_id) > 1})
        raise ValueError(f"campaign manifest.models declares duplicate block_id(s): {duplicates}")
    if "gemma" not in models[0].model.lower():
        raise ValueError(
            "campaign manifest.models[0] must be the Gemma block -- Plan 2 requires Gemma "
            f"before Qwen; got model={models[0].model!r}"
        )
    if "qwen" not in models[1].model.lower():
        raise ValueError(
            "campaign manifest.models[1] must be the Qwen block -- Plan 2 requires Gemma "
            f"before Qwen; got model={models[1].model!r}"
        )

    arms = tuple(
        _load_treatment_arm(a, f"campaign manifest.arms[{i}]")
        for i, a in enumerate(_require_list(raw["arms"], "campaign manifest.arms"))
    )
    _require_plan2_arms(arms)

    seeds = tuple(
        _require_int(s, f"campaign manifest.seeds[{i}]")
        for i, s in enumerate(_require_list(raw["seeds"], "campaign manifest.seeds"))
    )
    if len(seeds) != PLAN2_SEED_COUNT:
        raise ValueError(f"campaign manifest.seeds must declare exactly 12 seeds for Plan 2; got {len(seeds)}")

    order = _require_str(raw["order"], "campaign manifest.order")
    if order != PLAN2_ORDER:
        raise ValueError(f"campaign manifest.order must be {PLAN2_ORDER!r} for Plan 2; got {order!r}")

    driver = _require_str(raw["driver"], "campaign manifest.driver")
    if driver != PLAN2_DRIVER:
        raise ValueError(f"campaign manifest.driver must be {PLAN2_DRIVER!r} for Plan 2; got {driver!r}")

    fresh_conversation_per_trial = _require_bool(
        raw["fresh_conversation_per_trial"], "campaign manifest.fresh_conversation_per_trial"
    )
    if not fresh_conversation_per_trial:
        raise ValueError("campaign manifest.fresh_conversation_per_trial must be true for Plan 2")

    retry_policy = _load_retry_policy(raw["retry_policy"], "campaign manifest.retry_policy")
    if retry_policy.max_attempts != 1:
        raise ValueError(
            "campaign manifest.retry_policy.max_attempts must be 1 for Plan 2 counted trials -- "
            f"a hidden backend retry would silently resample a model episode; got {retry_policy.max_attempts}"
        )

    max_steps = _require_int(raw["max_steps"], "campaign manifest.max_steps")
    if max_steps != PLAN2_MAX_STEPS:
        raise ValueError(f"campaign manifest.max_steps must be 8 for Plan 2; got {max_steps}")

    result_char_cap = _require_int(raw["result_char_cap"], "campaign manifest.result_char_cap")

    audit_indices = tuple(
        _require_int(a, f"campaign manifest.audit_indices[{i}]")
        for i, a in enumerate(_require_list(raw["audit_indices"], "campaign manifest.audit_indices"))
    )
    if len(audit_indices) != PLAN2_AUDIT_COUNT:
        raise ValueError(
            f"campaign manifest.audit_indices must declare exactly six audit indices for Plan 2; "
            f"got {len(audit_indices)}"
        )

    prompt = _require_str(raw["prompt"], "campaign manifest.prompt")
    campaign_id = _require_str(raw["campaign_id"], "campaign manifest.campaign_id")
    campaign_schema_version = _require_str(
        raw["campaign_schema_version"], "campaign manifest.campaign_schema_version"
    )
    position = _require_str(raw["position"], "campaign manifest.position")

    position_provenance = _resolve_position_provenance(
        _require_str(raw["position_provenance"], "campaign manifest.position_provenance"), base_dir
    )
    contracts = _load_contract_versions_file(
        _require_str(raw["contracts"], "campaign manifest.contracts"), base_dir
    )
    rules = _load_calibration_rules(raw["rules"], "campaign manifest.rules")
    # A6 (external review): `required_audits_per_arm` is frozen into the
    # lock but was never checked against the actual `audit_indices` count
    # declared alongside it -- `compile_campaign_schedule` enforces that the
    # chosen audit indices are balanced PER ARM, but not that there are
    # exactly `required_audits_per_arm` of them. A campaign could freeze
    # `required_audits_per_arm=3` while `audit_indices` only ever supplies
    # 1 per arm, silently under-auditing every block.
    expected_audit_count = len(arms) * rules.required_audits_per_arm
    if len(audit_indices) != expected_audit_count:
        raise ValueError(
            f"campaign manifest.audit_indices has {len(audit_indices)} entries, but "
            f"rules.required_audits_per_arm={rules.required_audits_per_arm} with "
            f"{len(arms)} arms requires exactly {expected_audit_count}"
        )

    campaign = CampaignManifest(
        campaign_id=campaign_id,
        campaign_schema_version=campaign_schema_version,
        position=position,
        position_provenance=position_provenance,
        models=models,
        arms=arms,
        seeds=seeds,
        order=order,
        driver=driver,
        fresh_conversation_per_trial=fresh_conversation_per_trial,
        retry_policy=retry_policy,
        max_steps=max_steps,
        result_char_cap=result_char_cap,
        audit_indices=audit_indices,
        prompt=prompt,
        contracts=contracts,
        rules=rules,
    )

    # Reuse compile_schedule's already-tested arm/order/audit-balance
    # validation instead of duplicating it: each block's local suite must
    # compile to a clean, balanced 24-trial schedule.
    for block in campaign.models:
        try:
            compile_schedule(suite_for_block(campaign, block))
        except ValueError as exc:
            raise ValueError(
                f"campaign manifest schedule for block {block.block_id!r} is invalid: {exc}"
            ) from exc

    return campaign


def suite_for_block(campaign: CampaignManifest, model_block: ModelBlockConfig) -> SuiteManifest:
    """Build the existing one-model `SuiteManifest` for one campaign block.

    One position, one model: `compile_schedule` therefore produces local
    trial indices 1-24 (12 seeds x 2 arms under ABBA) with the campaign's
    shared, pre-balanced audit indices -- the same for every block.
    """
    return SuiteManifest(
        suite_id=f"{campaign.campaign_id}:{model_block.block_id}",
        driver=campaign.driver,
        positions=(campaign.position,),
        models=(model_block.model,),
        arms=campaign.arms,
        seeds=campaign.seeds,
        order=campaign.order,
        sampling=model_block.sampling,
        max_steps=campaign.max_steps,
        result_char_cap=campaign.result_char_cap,
        audit_indices=campaign.audit_indices,
    )


def _validate_resolved_schema_names(schemas: Sequence[Mapping[str, object]], arm_id: str) -> list[str]:
    names = [schema["function"]["name"] for schema in schemas]
    if "end_turn" in names:
        raise ValueError(
            f"arm {arm_id!r} resolved schema exposes end_turn, which benchmark trials must never expose"
        )
    if FINISH_TRIAL_TOOL_NAME not in names:
        raise ValueError(f"arm {arm_id!r} resolved schema is missing {FINISH_TRIAL_TOOL_NAME!r}")
    return names


def tool_surface_identity(
    tools_by_arm: Mapping[str, Sequence[Mapping[str, object]]]
) -> dict[str, list[dict[str, object]]]:
    """arm -> ordered [{"name": ..., "capability_id": ToolDef.requires}].

    Omits descriptions and JSON schemas on purpose: this identity is stable
    under a schema-text-only edit, which is exactly what makes it safe to
    live in the immutable campaign lock rather than the per-block session
    lock (see this module's docstring).
    """
    identity: dict[str, list[dict[str, object]]] = {}
    for arm_id, schemas in tools_by_arm.items():
        names = _validate_resolved_schema_names(schemas, arm_id)
        identity[arm_id] = [
            {
                "name": name,
                "capability_id": TOOL_REGISTRY[name].requires if name in TOOL_REGISTRY else None,
            }
            for name in names
        ]
    return identity


def tool_input_identity(
    tools_by_arm: Mapping[str, Sequence[Mapping[str, object]]]
) -> dict[str, list[dict[str, object]]]:
    """arm -> the complete canonical function schemas sent to the model,
    verbatim. Changing this must start a new counted session (block
    admission evidence), never silently rewrite the campaign lock."""
    identity: dict[str, list[dict[str, object]]] = {}
    for arm_id, schemas in tools_by_arm.items():
        _validate_resolved_schema_names(schemas, arm_id)
        identity[arm_id] = [dict(schema) for schema in schemas]
    return identity


def fingerprint_identity(value: object) -> str:
    return fingerprint(value)


def scorer_source_fingerprint(repo_root: Path) -> str:
    """Canonical digest of the exact scorer implementation: each scorer
    source file's repo-relative path plus its raw bytes. A missing file
    raises naming that exact file rather than silently omitting it from the
    digest (which would produce a valid-looking fingerprint for a partial,
    unauditable scorer)."""
    repo_root = Path(repo_root)
    entries = []
    for rel_path in SCORER_SOURCE_RELATIVE_PATHS:
        full_path = repo_root / rel_path
        if not full_path.is_file():
            raise ValueError(f"scorer source file is missing: {rel_path} (expected at {full_path})")
        entries.append(
            {
                "path": rel_path.as_posix(),
                "sha256": hashlib.sha256(full_path.read_bytes()).hexdigest(),
            }
        )
    return fingerprint(entries)


def write_contract_candidate(path: Path, versions: ContractVersions, repo_root: Path) -> dict[str, object]:
    """Atomically write one candidate instrument contract.

    Independently recomputes `scorer_source_fingerprint(repo_root)` and
    requires it to match `versions.scorer_fingerprint` before writing
    anything -- a caller-supplied fingerprint that no longer matches the
    checked-out scorer source must never be committed to disk.
    """
    expected_scorer_fingerprint = scorer_source_fingerprint(repo_root)
    if versions.scorer_fingerprint != expected_scorer_fingerprint:
        raise ValueError(
            "scorer_fingerprint does not match the scorer source at repo_root: "
            f"expected {expected_scorer_fingerprint}, got {versions.scorer_fingerprint}"
        )

    payload = {
        "evidence_schema_version": versions.evidence_schema_version,
        "predicate_schema_version": versions.predicate_schema_version,
        "report_schema_version": versions.report_schema_version,
        "scorer_fingerprint": versions.scorer_fingerprint,
    }

    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_name(f".{out_path.name}.tmp")
    data = yaml.safe_dump(payload, sort_keys=True, default_flow_style=False).encode("utf-8")
    with open(tmp_path, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, out_path)

    return payload


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m civ_mcp.arena.benchmark_contract")
    subparsers = parser.add_subparsers(dest="command", required=True)

    freeze = subparsers.add_parser("freeze", help="write a candidate instrument contract")
    freeze.add_argument("--evidence-version", required=True)
    freeze.add_argument("--predicate-version", required=True)
    freeze.add_argument("--report-version", required=True)
    freeze.add_argument("--output", required=True)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    if args.command == "freeze":
        repo_root = _repo_root()
        scorer_fingerprint = scorer_source_fingerprint(repo_root)
        versions = ContractVersions(
            evidence_schema_version=args.evidence_version,
            predicate_schema_version=args.predicate_version,
            report_schema_version=args.report_version,
            scorer_fingerprint=scorer_fingerprint,
        )
        write_contract_candidate(Path(args.output), versions, repo_root)
        return 0
    raise ValueError(f"unknown command: {args.command!r}")  # pragma: no cover -- argparse enforces choices


if __name__ == "__main__":
    raise SystemExit(main())
