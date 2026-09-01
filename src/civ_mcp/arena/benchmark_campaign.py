"""Campaign-level lock, schedule, and store for the arena two-model counted
calibration campaign.

Plan 2 (see `benchmark_contract.py`'s module docstring) freezes one campaign
against exactly two model blocks before any counted trial runs. That module
is the strict loader for the frozen `CampaignManifest`; this module is the
second half of the story -- it:

- compiles both model blocks' local 24-trial schedules from one manifest
  (`compile_campaign_schedule`);
- builds the single immutable lock shared by both blocks, fingerprinted over
  every frozen campaign-wide input (`build_campaign_lock`);
- provides `CampaignStore`, the on-disk two-level artifact layout that ties
  the campaign lock to each block's own run directory.

Two-level artifact layout (spec-locked -- the per-model block lock is the
EVOLVED existing `session.json`; no third lock artifact is ever introduced):

    benchmark_runs/<campaign-id>/
        campaign.json               the immutable campaign lock (this module)
        schedule.json                compile_campaign_schedule(campaign)
        campaign-journal.jsonl       append-only campaign-level event log
        admissions/<block-id>-attempt-NNN.json   per-block admission evidence
        blocks/<block-id>/session.json   the per-block lock -- an ordinary
                                          `BenchmarkStore` run directory, built
                                          by `benchmark_gates.build_session_lock`
        blocks/<block-id>/schedule.json  that block's local 24-trial schedule
        blocks/<block-id>/journal.jsonl
        blocks/<block-id>/attempts/
        blocks/<block-id>/trials/

`CampaignStore` wraps `BenchmarkStore`; it never reimplements trial storage,
atomic commit, or the journal sequence. `open_block` hands the block
directory straight to `BenchmarkStore.create`, which already knows how to
create a run directory or reattach to one whose `session.json` already
matches byte-for-byte -- `CampaignStore` only additionally manages that
block's own `schedule.json` sibling file (the same way the campaign root
manages its own `schedule.json` next to `campaign.json`).
"""
from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import Mapping

from civ_mcp.arena.benchmark_agent import BENCHMARK_SYSTEM
from civ_mcp.arena.benchmark_contract import CampaignManifest, suite_for_block
from civ_mcp.arena.benchmark_gates import GateFailure
from civ_mcp.arena.benchmark_manifest import PositionManifest, fingerprint
from civ_mcp.arena.benchmark_schedule import compile_schedule
from civ_mcp.arena.benchmark_store import BenchmarkStore, canonical_json_bytes

__all__ = [
    "BenchmarkCampaignError",
    "CampaignLockMismatchError",
    "CampaignStore",
    "build_campaign_lock",
    "compile_campaign_schedule",
]


class BenchmarkCampaignError(Exception):
    """Base class for `CampaignStore` errors."""


class CampaignLockMismatchError(BenchmarkCampaignError):
    """Raised when a provided campaign lock, campaign schedule, or per-block
    schedule does not match what is already recorded on disk byte-for-byte
    (canonical JSON) on reopen."""


# H1 (external review wave H): the canonical encoding behind every
# byte-for-byte lock/schedule comparison in this module is now the SHARED
# public `benchmark_store.canonical_json_bytes` -- the reporters
# (`benchmark_campaign_report.build_campaign_report`,
# `benchmark_admission.block_is_complete`) re-verify `open_block`'s
# blocks/<id>/schedule.json == campaign schedule entry invariant at read
# time, and writer and readers must agree on one encoding rather than
# maintaining drifting private copies.
_canonical_bytes = canonical_json_bytes


def _fsync_write_bytes(path: Path, data: bytes) -> None:
    with open(path, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


def compile_campaign_schedule(campaign: CampaignManifest) -> dict[str, object]:
    """Compile every model block's local 24-trial schedule from one
    `CampaignManifest`.

    Each block's schedule is `compile_schedule(suite_for_block(campaign,
    block))` -- one position, one model -- so local trial indices always run
    1..24 for every block; this function never renumbers or reorders them
    across blocks. Returns a canonical, JSON-safe
    `{"blocks": {block_id: {"trials": [...]}}}` payload.
    """
    blocks: dict[str, object] = {}
    for block in campaign.models:
        trials = compile_schedule(suite_for_block(campaign, block))
        blocks[block.block_id] = {"trials": [dataclasses.asdict(t) for t in trials]}
    return {"blocks": blocks}


def _require_nonempty(value: object, *, code: str, message: str) -> None:
    if not value:
        raise GateFailure(code, {"message": message})


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
) -> dict[str, object]:
    """Build the single immutable lock shared by both model blocks.

    The lock includes: campaign schema version, the non-empty clean WSL
    commit expected on both checkouts (`expected_commit`), save
    archive/state/provenance digests, full rubric/objectives, prompt, arms,
    seeds/order, driver/fresh-context rule, model configurations, retry
    policy, audit indices, calibration rules, all contract versions, the
    scorer fingerprint, and the tool-surface identity. It deliberately does
    NOT include exact input tool schemas (`tool_input_identity`): schema
    text is block *admission* evidence (the per-block session lock), so
    changing it starts a new counted session without rewriting the
    scientific campaign -- see `benchmark_contract`'s module docstring.

    `campaign_fingerprint` is computed over every other field.

    Rejects, fail-closed:

    - a missing/empty digest (`expected_commit` / `prompt_fingerprint` /
      `rubric_fingerprint` / `tool_surface_fingerprint`, or any contract
      version field);
    - a non-empty treatment-arm option (Plan 2 requires arms with no
      per-arm option overrides -- `load_campaign_manifest` already enforces
      this; checked again here, defense-in-depth, since this function does
      not require its caller to have gone through that loader);
    - `campaign.position` disagreeing with `position.position_id`;
    - `position_provenance` disagreeing with the file
      `campaign.position_provenance` actually points at, or its own
      declared `archive_sha256` disagreeing with `position.archive_sha256`;
    - `schedule` disagreeing with an independently recompiled
      `compile_campaign_schedule(campaign)`;
    - `prompt_fingerprint` / `rubric_fingerprint` disagreeing with digests
      independently recomputed from `campaign.prompt` / `position.rubric`.
    """
    _require_nonempty(
        expected_commit,
        code="missing_expected_commit",
        message="campaign lock is missing a non-empty expected_commit",
    )
    _require_nonempty(
        prompt_fingerprint,
        code="missing_digest",
        message="campaign lock is missing a non-empty prompt_fingerprint",
    )
    _require_nonempty(
        rubric_fingerprint,
        code="missing_digest",
        message="campaign lock is missing a non-empty rubric_fingerprint",
    )
    _require_nonempty(
        tool_surface_fingerprint,
        code="missing_digest",
        message="campaign lock is missing a non-empty tool_surface_fingerprint",
    )

    contract_fields = dataclasses.asdict(campaign.contracts)
    missing_contract_fields = sorted(k for k, v in contract_fields.items() if not v)
    if missing_contract_fields:
        raise GateFailure(
            "missing_digest",
            {
                "missing": missing_contract_fields,
                "message": (
                    f"campaign contracts are missing required field(s): {missing_contract_fields}"
                ),
            },
        )

    nonempty_option_arms = [arm.arm_id for arm in campaign.arms if arm.options]
    if nonempty_option_arms:
        raise GateFailure(
            "nonempty_treatment_option",
            {
                "arm_ids": nonempty_option_arms,
                "message": (
                    "campaign lock refuses non-empty treatment-arm options for "
                    f"arm(s) {nonempty_option_arms}"
                ),
            },
        )

    if campaign.position != position.position_id:
        raise GateFailure(
            "campaign_position_mismatch",
            {
                "campaign_position": campaign.position,
                "position_id": position.position_id,
                "message": (
                    f"campaign declares position {campaign.position!r} but was given "
                    f"position manifest {position.position_id!r}"
                ),
            },
        )

    provenance_path = Path(campaign.position_provenance)
    try:
        on_disk_provenance = json.loads(provenance_path.read_text())
    except OSError as exc:
        raise GateFailure(
            "provenance_unreadable",
            {"path": str(provenance_path), "message": str(exc)},
        ) from exc
    if on_disk_provenance != dict(position_provenance):
        raise GateFailure(
            "provenance_mismatch",
            {
                "path": str(provenance_path),
                "message": (
                    f"provided position_provenance does not match the content of "
                    f"{provenance_path}"
                ),
            },
        )

    provenance_archive_sha256 = position_provenance.get("archive_sha256")
    if not provenance_archive_sha256:
        raise GateFailure(
            "missing_digest",
            {
                "missing": ["provenance.archive_sha256"],
                "message": "position_provenance is missing a non-empty archive_sha256",
            },
        )
    if provenance_archive_sha256 != position.archive_sha256:
        raise GateFailure(
            "provenance_archive_digest_mismatch",
            {
                "provenance_archive_sha256": provenance_archive_sha256,
                "position_archive_sha256": position.archive_sha256,
                "message": (
                    "position_provenance.archive_sha256 does not match "
                    "position.archive_sha256"
                ),
            },
        )

    expected_schedule = compile_campaign_schedule(campaign)
    if dict(schedule) != expected_schedule:
        raise GateFailure(
            "schedule_mismatch",
            {
                "message": (
                    "provided schedule does not match an independently recompiled "
                    "compile_campaign_schedule(campaign)"
                ),
            },
        )

    expected_prompt_fingerprint = fingerprint({"system": BENCHMARK_SYSTEM, "user": campaign.prompt})
    if prompt_fingerprint != expected_prompt_fingerprint:
        raise GateFailure(
            "prompt_fingerprint_mismatch",
            {"message": "prompt_fingerprint does not match campaign.prompt"},
        )

    expected_rubric_fingerprint = fingerprint(list(position.rubric))
    if rubric_fingerprint != expected_rubric_fingerprint:
        raise GateFailure(
            "rubric_fingerprint_mismatch",
            {"message": "rubric_fingerprint does not match position.rubric"},
        )

    lock: dict[str, object] = {
        "campaign_id": campaign.campaign_id,
        "campaign_schema_version": campaign.campaign_schema_version,
        "expected_commit": expected_commit,
        "position_id": position.position_id,
        "digests": {
            "archive_sha256": position.archive_sha256,
            "expected_state_sha256": position.expected_state_sha256,
            "provenance": fingerprint(dict(position_provenance)),
            "prompt": prompt_fingerprint,
            "rubric": rubric_fingerprint,
            "schedule": fingerprint(expected_schedule),
        },
        "rubric": list(position.rubric),
        "objectives": list(position.objectives),
        "prompt": campaign.prompt,
        "arms": [dataclasses.asdict(arm) for arm in campaign.arms],
        "seeds": list(campaign.seeds),
        "order": campaign.order,
        "driver": campaign.driver,
        "fresh_conversation_per_trial": campaign.fresh_conversation_per_trial,
        "models": [dataclasses.asdict(model) for model in campaign.models],
        "retry_policy": dataclasses.asdict(campaign.retry_policy),
        "max_steps": campaign.max_steps,
        "result_char_cap": campaign.result_char_cap,
        "audit_indices": list(campaign.audit_indices),
        "rules": dataclasses.asdict(campaign.rules),
        "contracts": contract_fields,
        "tool_surface_fingerprint": tool_surface_fingerprint,
        "ok": True,
    }
    lock["campaign_fingerprint"] = fingerprint(lock)
    return lock


class CampaignStore:
    """The two-level on-disk artifact layout for one campaign. Wraps
    `BenchmarkStore`; never reimplements trial storage."""

    CAMPAIGN_FILE = "campaign.json"
    SCHEDULE_FILE = "schedule.json"
    JOURNAL_FILE = "campaign-journal.jsonl"
    ADMISSIONS_DIR = "admissions"
    BLOCKS_DIR = "blocks"

    def __init__(
        self, root: Path, lock: dict, schedule: dict, *, fingerprint: str | None
    ) -> None:
        self.root = Path(root)
        self.lock = dict(lock)
        self.schedule = dict(schedule)
        self.fingerprint = fingerprint

    # -- construction -------------------------------------------------------

    @classmethod
    def create(cls, root: str | Path, campaign_lock: dict, schedule: dict) -> "CampaignStore":
        """Create a new campaign directory (or reattach to one whose
        `campaign.json`/`schedule.json` already match `campaign_lock`/
        `schedule` byte-for-byte). Raises `CampaignLockMismatchError` if the
        campaign directory exists with a different lock or schedule."""
        return cls._open_or_create(root, campaign_lock, schedule, creating=True)

    @classmethod
    def open(cls, root: str | Path, campaign_lock: dict, schedule: dict) -> "CampaignStore":
        """Reopen an existing campaign directory. Raises
        `CampaignLockMismatchError` if `campaign_lock`/`schedule` do not
        match what is stored byte-for-byte, or `FileNotFoundError` if no
        campaign was ever created there."""
        return cls._open_or_create(root, campaign_lock, schedule, creating=False)

    @classmethod
    def _open_or_create(
        cls, root: str | Path, campaign_lock: dict, schedule: dict, *, creating: bool
    ) -> "CampaignStore":
        if not campaign_lock.get("campaign_fingerprint"):
            raise BenchmarkCampaignError(
                "campaign lock is missing a non-empty 'campaign_fingerprint' -- "
                "refusing to create or open a campaign without one"
            )

        root = Path(root)
        campaign_path = root / cls.CAMPAIGN_FILE
        schedule_path = root / cls.SCHEDULE_FILE
        provided_lock = _canonical_bytes(campaign_lock)
        provided_schedule = _canonical_bytes(schedule)

        if campaign_path.exists():
            if campaign_path.read_bytes() != provided_lock:
                raise CampaignLockMismatchError(
                    f"campaign lock mismatch at {campaign_path}: provided lock does "
                    "not match the recorded campaign byte-for-byte"
                )
        elif creating:
            root.mkdir(parents=True, exist_ok=True)
            _fsync_write_bytes(campaign_path, provided_lock)
        else:
            raise FileNotFoundError(f"no campaign found at {campaign_path}")

        if schedule_path.exists():
            if schedule_path.read_bytes() != provided_schedule:
                raise CampaignLockMismatchError(
                    f"campaign schedule mismatch at {schedule_path}: provided "
                    "schedule does not match the recorded schedule byte-for-byte"
                )
        elif creating:
            _fsync_write_bytes(schedule_path, provided_schedule)
        else:
            raise FileNotFoundError(f"no campaign schedule found at {schedule_path}")

        (root / cls.ADMISSIONS_DIR).mkdir(parents=True, exist_ok=True)
        (root / cls.BLOCKS_DIR).mkdir(parents=True, exist_ok=True)
        (root / cls.JOURNAL_FILE).touch(exist_ok=True)

        return cls(
            root,
            campaign_lock,
            schedule,
            fingerprint=campaign_lock.get("campaign_fingerprint"),
        )

    # -- admissions (per-block admission evidence; append-only) --------------

    def _admission_attempt_count(self, block_id: str) -> int:
        admissions_dir = self.root / self.ADMISSIONS_DIR
        if not admissions_dir.is_dir():
            return 0
        prefix = f"{block_id}-attempt-"
        return sum(
            1
            for path in admissions_dir.iterdir()
            if path.is_file() and path.name.startswith(prefix) and path.name.endswith(".json")
        )

    def record_admission(self, block_id: str, evidence: dict) -> Path:
        """Allocate the next append-only `admissions/<block-id>-attempt-
        NNN.json`. Never overwrites a prior attempt -- a crash mid-write
        leaves no half-written file behind (temp file, fsync, atomic
        `os.replace`, same convention as `BenchmarkStore.record_attempt`).

        G4 (external review wave G): every record written here -- ordinary
        admission attempts, remediation records, and disposition records
        alike -- is stamped with this store's own `campaign_fingerprint`,
        binding it to the campaign it was actually written under. The
        reporter's deferral-corroboration scan
        (`benchmark_campaign_report._has_valid_replication_deferred_admission`)
        ignores any record whose stamp is absent or names a different
        campaign, so a record forged into (or copied between) run dirs can
        never corroborate a deferral. A store attached without a recorded
        campaign lock yet (`fingerprint=None` -- the pre-campaign
        remediation journal target, see
        `benchmark_runner._load_remediation_journal_target`) writes its
        record unstamped: still journaled diagnostics, deliberately never
        corroboration-grade (a pre-campaign remediation cannot have a
        preceding same-code admission failure anyway).
        """
        admissions_dir = self.root / self.ADMISSIONS_DIR
        admissions_dir.mkdir(parents=True, exist_ok=True)
        payload = dict(evidence)
        if self.fingerprint:
            payload["campaign_fingerprint"] = self.fingerprint
        ordinal = self._admission_attempt_count(block_id) + 1
        dest = admissions_dir / f"{block_id}-attempt-{ordinal:03d}.json"
        tmp = admissions_dir / f".{block_id}-attempt-{ordinal:03d}.json.tmp"
        _fsync_write_bytes(tmp, _canonical_bytes(payload))
        if dest.exists():
            tmp.unlink(missing_ok=True)
            raise BenchmarkCampaignError(
                f"admission attempt already recorded at {dest}; record_admission "
                "never overwrites a prior attempt"
            )
        os.replace(tmp, dest)
        return dest

    # -- blocks (per-model-block run directories; owned by BenchmarkStore) --

    def open_block(self, block_id: str, session_lock: dict, schedule: dict) -> BenchmarkStore:
        """Open (creating if necessary) the `BenchmarkStore` run directory
        for one model block.

        Passes the block directory straight to `BenchmarkStore.create`,
        which already knows how to create a run directory or reattach to
        one whose `session.json` matches `session_lock` byte-for-byte --
        this never creates a second lock file. The block's own
        `schedule.json` sibling file is this method's own responsibility
        (mirroring how the campaign root manages its own `schedule.json`
        next to `campaign.json`), verified/written the same way.

        This is the one join point where both this store's own campaign
        identity (`self.fingerprint`) and a caller-supplied `session_lock`
        are in hand at the same time -- `session_lock["campaign_fingerprint"]`
        must be present and must equal `self.fingerprint`, or this method
        refuses before touching disk. Without this check a session lock
        built against some OTHER campaign (a different `campaign_fingerprint`)
        would be silently accepted here, writing a block under THIS
        campaign whose `session.json` actually references a different one --
        exactly the kind of stale/mismatched provenance every other check in
        this module (and `BenchmarkStore.is_trial_complete`) fails closed on.
        """
        session_campaign_fingerprint = session_lock.get("campaign_fingerprint")
        if not session_campaign_fingerprint or session_campaign_fingerprint != self.fingerprint:
            raise CampaignLockMismatchError(
                f"session lock for block {block_id!r} declares campaign_fingerprint "
                f"{session_campaign_fingerprint!r}, but this campaign's fingerprint is "
                f"{self.fingerprint!r}; refusing to open a block under a campaign it "
                "does not belong to"
            )

        block_dir = self.root / self.BLOCKS_DIR / block_id
        schedule_path = block_dir / "schedule.json"
        provided_schedule = _canonical_bytes(schedule)

        if schedule_path.exists():
            if schedule_path.read_bytes() != provided_schedule:
                raise CampaignLockMismatchError(
                    f"block schedule mismatch at {schedule_path}: provided schedule "
                    "does not match the recorded schedule byte-for-byte"
                )
        else:
            block_dir.mkdir(parents=True, exist_ok=True)
            _fsync_write_bytes(schedule_path, provided_schedule)

        return BenchmarkStore.create(block_dir, session_lock)
