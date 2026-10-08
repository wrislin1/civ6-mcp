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

# Repository-relative files whose bytes define v2 behaviour: scorer and
# classifier, tool schemas and dispatch, store/runner, query (GameState/Lua),
# reload/deployment, and position helpers. Sorted; later tasks append the
# modules they introduce (keep it sorted).
FINGERPRINT_DEPENDENCIES: tuple[str, ...] = tuple(
    sorted(
        (
            "src/civ_mcp/arena/action_metrics.py",
            "src/civ_mcp/arena/benchmark_agent.py",
            "src/civ_mcp/arena/benchmark_audit.py",
            "src/civ_mcp/arena/benchmark_capture.py",
            "src/civ_mcp/arena/benchmark_contract_v2.py",
            "src/civ_mcp/arena/benchmark_deploy.py",
            "src/civ_mcp/arena/benchmark_ledger.py",
            "src/civ_mcp/arena/benchmark_lifecycle.py",
            "src/civ_mcp/arena/benchmark_manifest_v2.py",
            "src/civ_mcp/arena/benchmark_position.py",
            "src/civ_mcp/arena/benchmark_predicates_v2.py",
            "src/civ_mcp/arena/benchmark_runner.py",
            "src/civ_mcp/arena/benchmark_scoring_v2.py",
            "src/civ_mcp/arena/benchmark_state.py",
            "src/civ_mcp/arena/benchmark_state_v2.py",
            "src/civ_mcp/arena/benchmark_store.py",
            "src/civ_mcp/arena/registry.py",
            "src/civ_mcp/connection.py",
            "src/civ_mcp/game_launcher.py",
            "src/civ_mcp/game_state.py",
            "src/civ_mcp/lua/benchmark.py",
            "src/civ_mcp/lua/benchmark_v2.py",
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


def implementation_fingerprint(root: Path) -> str:
    """Hash the explicit dependency list (path + bytes) under `root`.

    Raises `ValueError` naming any dependency that is not a file.
    """
    root = Path(root)
    entries = []
    for rel in FINGERPRINT_DEPENDENCIES:
        path = root / rel
        if not path.is_file():
            raise ValueError(f"fingerprint dependency missing: {rel}")
        entries.append({"path": rel, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return document_digest({"v2_dependencies": entries})
