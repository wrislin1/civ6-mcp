"""Version-2 canonical hashing and implementation fingerprint.

The version-2 evidence/scoring contract lives beside, never inside, the
version-1 `benchmark_contract.py`. This module only provides the primitives
every v2 document and report shares: canonical JSON bytes, a document digest,
and an implementation fingerprint over an explicit dependency list. The v2
fingerprint is a different construction from the released v1 fingerprint and
must never be presented as equal to it.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

# Repository-relative files whose bytes determine a score, a classification
# or a piece of scored evidence: hashing/manifest, state capture and query,
# predicates, lifecycle, scoring, ledger, audit, reports, validation, the
# actor/scripted runners and schedule, store, tool registry, positions and
# the FireTuner transport. A change here is a new `contract_identity`, and
# every affected packet is revalidated (see benchmarks/contracts/
# instrument-v2.md, "Amendments"). Sorted.
FINGERPRINT_DEPENDENCIES: tuple[str, ...] = tuple(
    sorted(
        (
            "src/civ_mcp/arena/action_metrics.py",
            "src/civ_mcp/arena/benchmark_agent.py",
            "src/civ_mcp/arena/benchmark_audit.py",
            "src/civ_mcp/arena/benchmark_capture.py",
            "src/civ_mcp/arena/benchmark_contract_v2.py",
            "src/civ_mcp/arena/benchmark_ledger.py",
            "src/civ_mcp/arena/benchmark_lifecycle.py",
            "src/civ_mcp/arena/benchmark_manifest_v2.py",
            "src/civ_mcp/arena/benchmark_position.py",
            "src/civ_mcp/arena/benchmark_predicates_v2.py",
            "src/civ_mcp/arena/benchmark_report_v2.py",
            "src/civ_mcp/arena/benchmark_runner.py",
            "src/civ_mcp/arena/benchmark_schedule.py",
            "src/civ_mcp/arena/benchmark_scoring_v2.py",
            "src/civ_mcp/arena/benchmark_scripted.py",
            "src/civ_mcp/arena/benchmark_scripted_runner.py",
            "src/civ_mcp/arena/benchmark_state.py",
            "src/civ_mcp/arena/benchmark_state_v2.py",
            "src/civ_mcp/arena/benchmark_store.py",
            "src/civ_mcp/arena/benchmark_validation.py",
            "src/civ_mcp/arena/registry.py",
            "src/civ_mcp/connection.py",
            "src/civ_mcp/lua/benchmark_v2.py",
            "src/civ_mcp/tuner_client.py",
        )
    )
)

# The authoring/gate/deployment toolkit: it produces and checks evidence but
# never changes what a score means. Its digest is recorded as
# `toolkit_identity` (preflight, gate) for information only -- it is never
# compared for equality, and a toolkit change triggers no revalidation.
TOOLKIT_DEPENDENCIES: tuple[str, ...] = tuple(
    sorted(
        (
            "src/civ_mcp/arena/benchmark_authoring.py",
            "src/civ_mcp/arena/benchmark_authoring_journal.py",
            "src/civ_mcp/arena/benchmark_capture_probe.py",
            "src/civ_mcp/arena/benchmark_deploy.py",
            "src/civ_mcp/arena/benchmark_part1_evidence.py",
            "src/civ_mcp/arena/benchmark_part1_gate.py",
            "src/civ_mcp/game_launcher.py",
            "src/civ_mcp/launcher_cli.py",
        )
    )
)


def canonical_bytes(value: Any) -> bytes:
    """Deterministic UTF-8 JSON: sorted keys, compact, no NaN/Infinity."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def document_digest(value: Any) -> str:
    """SHA-256 hex digest of `canonical_bytes(value)`."""
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _dependency_entries(root: Path, dependencies: tuple[str, ...]) -> list[dict[str, str]]:
    root = Path(root)
    entries = []
    for rel in dependencies:
        path = root / rel
        if not path.is_file():
            raise ValueError(f"fingerprint dependency missing: {rel}")
        entries.append({"path": rel, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return entries


def implementation_fingerprint(root: Path) -> str:
    """Hash the explicit dependency list (path + bytes) under `root`.

    Raises `ValueError` naming any dependency that is not a file.
    """
    return document_digest({"v2_dependencies": _dependency_entries(root,
                                                                   FINGERPRINT_DEPENDENCIES)})


def toolkit_fingerprint(root: Path) -> str:
    """Informational digest of `TOOLKIT_DEPENDENCIES` (never gated for equality)."""
    return document_digest({"v2_toolkit": _dependency_entries(root, TOOLKIT_DEPENDENCIES)})
