"""Tests for the version-2 canonical hashing and implementation fingerprint."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from civ_mcp.arena import benchmark_contract_v2 as c2

REPO = Path(__file__).resolve().parents[2]


def test_canonical_bytes_is_sorted_compact_utf8():
    assert c2.canonical_bytes({"b": 1, "a": "é"}) == '{"a":"é","b":1}'.encode()


def test_canonical_bytes_rejects_nan():
    with pytest.raises(ValueError):
        c2.canonical_bytes({"x": float("nan")})


def test_document_digest_is_key_order_independent():
    assert c2.document_digest({"a": 1, "b": [1, 2]}) == c2.document_digest({"b": [1, 2], "a": 1})
    assert c2.document_digest({"a": 1}) != c2.document_digest({"a": 2})
    assert len(c2.document_digest({})) == 64


def test_dependency_list_is_sorted_unique_and_exists():
    deps = list(c2.FINGERPRINT_DEPENDENCIES)
    assert deps == sorted(set(deps))
    for rel in deps:
        assert (REPO / rel).is_file(), rel
    assert "src/civ_mcp/arena/benchmark_contract_v2.py" in deps
    assert "src/civ_mcp/arena/benchmark_manifest_v2.py" in deps


def _copy_tree(tmp_path: Path) -> Path:
    for rel in c2.FINGERPRINT_DEPENDENCIES:
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, dest)
    return tmp_path


def test_fingerprint_is_stable_and_matches_repo_copy(tmp_path):
    root = _copy_tree(tmp_path)
    assert c2.implementation_fingerprint(root) == c2.implementation_fingerprint(REPO)


@pytest.mark.parametrize(
    "rel",
    [
        "src/civ_mcp/arena/action_metrics.py",  # scorer / classifier
        "src/civ_mcp/arena/registry.py",  # tool schemas
        "src/civ_mcp/arena/benchmark_agent.py",  # dispatch
        "src/civ_mcp/lua/benchmark.py",  # query
    ],
)
def test_editing_a_dependency_changes_fingerprint(tmp_path, rel):
    root = _copy_tree(tmp_path)
    before = c2.implementation_fingerprint(root)
    with (root / rel).open("a") as f:
        f.write("\n# edit\n")
    assert c2.implementation_fingerprint(root) != before


def test_missing_dependency_raises(tmp_path):
    root = _copy_tree(tmp_path)
    (root / "src/civ_mcp/arena/benchmark_runner.py").unlink()
    with pytest.raises(ValueError, match="benchmark_runner"):
        c2.implementation_fingerprint(root)
