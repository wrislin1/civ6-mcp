"""Native immutable save export and its WSL bridge wrapper.

Offline only: ``SINGLE_SAVE_DIR`` points at a temp directory, sleep/clock
are injected, and the Windows bridge is faked via ``os.path.exists`` and
``subprocess.run`` on ``benchmark_deploy``.
"""

from __future__ import annotations

import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from civ_mcp import game_launcher
from civ_mcp.arena import benchmark_deploy

BASE_SAVE = "SEONDEOK 100 400 BC.Civ6Save"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def saves(tmp_path, monkeypatch):
    saves_dir = tmp_path / "Single"
    saves_dir.mkdir()
    monkeypatch.setattr(game_launcher, "SINGLE_SAVE_DIR", str(saves_dir))
    return saves_dir


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _export(name, destination, **kwargs):
    fake = kwargs.pop("fake", None) or FakeTime()
    return game_launcher.export_benchmark_save(
        name, str(destination), sleep=fake.sleep, clock=fake.clock, **kwargs
    )


# --- native export --------------------------------------------------------


def test_export_copies_base_save_with_spaces_and_verifies_digest(saves, tmp_path):
    payload = b"civ6 save bytes" * 1000
    (saves / BASE_SAVE).write_bytes(payload)
    dest = tmp_path / "archive" / "base.Civ6Save"
    dest.parent.mkdir()

    result = _export("SEONDEOK 100 400 BC", dest)

    assert dest.read_bytes() == payload
    assert result == {
        "save_name": BASE_SAVE,
        "source_path": os.path.join(str(saves), BASE_SAVE),
        "dest_path": str(dest),
        "sha256": _sha(payload),
        "size": len(payload),
        "existed": False,
    }
    assert [p.name for p in dest.parent.iterdir()] == ["base.Civ6Save"]


@pytest.mark.parametrize(
    "bad_name",
    ["", "../evil", "sub/name", "sub\\name", "..", "a..b", "C:name", ".Civ6Save"],
)
def test_export_rejects_separators_and_traversal(saves, tmp_path, bad_name):
    with pytest.raises(ValueError, match="unsafe save name"):
        _export(bad_name, tmp_path / "out.Civ6Save")
    assert not (tmp_path / "out.Civ6Save").exists()


def test_export_times_out_on_a_save_that_never_stabilises(saves, tmp_path):
    source = saves / "PARTIAL.Civ6Save"
    source.write_bytes(b"")
    fake = FakeTime()
    growth = iter(range(1, 10_000))

    def growing_sleep(seconds):
        fake.sleep(seconds)
        with open(source, "ab") as fh:  # Network.SaveGame still writing
            fh.write(b"x" * next(growth))

    with pytest.raises(TimeoutError):
        game_launcher.export_benchmark_save(
            "PARTIAL", str(tmp_path / "out.Civ6Save"),
            sleep=growing_sleep, clock=fake.clock, timeout_s=5.0,
        )
    assert fake.now <= 5.0 + game_launcher._STABLE_POLL_S
    assert not (tmp_path / "out.Civ6Save").exists()


def test_export_times_out_when_save_never_appears(saves, tmp_path):
    fake = FakeTime()
    with pytest.raises(TimeoutError):
        _export("MISSING", tmp_path / "out.Civ6Save", fake=fake, timeout_s=3.0)
    assert fake.sleeps  # it polled rather than failing on the first miss


def test_export_waits_for_an_empty_save_to_become_complete(saves, tmp_path):
    source = saves / "LATE.Civ6Save"
    source.write_bytes(b"")
    fake = FakeTime()
    writes = iter([b"part", b"ial", b"", b"", b""])

    def writing_sleep(seconds):
        fake.sleep(seconds)
        chunk = next(writes)
        if chunk:
            with open(source, "ab") as fh:
                fh.write(chunk)

    result = game_launcher.export_benchmark_save(
        "LATE", str(tmp_path / "out.Civ6Save"), sleep=writing_sleep, clock=fake.clock
    )
    assert result["sha256"] == _sha(b"partial")
    assert (tmp_path / "out.Civ6Save").read_bytes() == b"partial"


def test_export_same_digest_destination_is_idempotent(saves, tmp_path):
    payload = b"stable"
    (saves / "S.Civ6Save").write_bytes(payload)
    dest = tmp_path / "out.Civ6Save"
    dest.write_bytes(payload)

    result = _export("S", dest)

    assert result["existed"] is True
    assert result["sha256"] == _sha(payload)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Single", "out.Civ6Save"]


def test_export_refuses_a_differing_existing_archive_and_leaves_it(saves, tmp_path):
    (saves / "S.Civ6Save").write_bytes(b"new")
    dest = tmp_path / "out.Civ6Save"
    dest.write_bytes(b"old archive")

    with pytest.raises(ValueError, match="differs"):
        _export("S", dest)

    assert dest.read_bytes() == b"old archive"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Single", "out.Civ6Save"]


def test_export_never_replaces_onto_an_existing_path(saves, tmp_path, monkeypatch):
    """Even if the destination appears between the check and the publish,
    the publish step must fail rather than overwrite."""
    (saves / "S.Civ6Save").write_bytes(b"ours")
    dest = tmp_path / "out.Civ6Save"
    real_link = os.link

    def racing_link(src, dst):
        with open(dst, "wb") as fh:
            fh.write(b"someone else's archive")
        return real_link(src, dst)

    monkeypatch.setattr(game_launcher.os, "link", racing_link)
    monkeypatch.setattr(
        game_launcher.os, "replace", lambda *a: pytest.fail("os.replace must not be used")
    )

    with pytest.raises(ValueError, match="differs"):
        _export("S", dest)
    assert dest.read_bytes() == b"someone else's archive"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Single", "out.Civ6Save"]


def test_export_rejects_expected_sha256_mismatch(saves, tmp_path):
    (saves / "S.Civ6Save").write_bytes(b"bytes")
    dest = tmp_path / "out.Civ6Save"

    with pytest.raises(ValueError, match="expected"):
        _export("S", dest, expected_sha256="0" * 64)
    assert not dest.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Single"]

    assert _export("S", dest, expected_sha256=_sha(b"bytes"))["existed"] is False


def test_export_rejects_source_that_changes_during_copy(saves, tmp_path, monkeypatch):
    source = saves / "S.Civ6Save"
    source.write_bytes(b"original")
    real_hash = game_launcher._sha256_file

    def mutating_hash(path):
        digest = real_hash(path)
        if str(path) == str(source):
            source.write_bytes(b"rewritten by the game")
        return digest

    monkeypatch.setattr(game_launcher, "_sha256_file", mutating_hash)

    with pytest.raises(ValueError, match="changed"):
        _export("S", tmp_path / "out.Civ6Save")
    assert not (tmp_path / "out.Civ6Save").exists()


# --- bridge wrapper -------------------------------------------------------


def _fake_bridge(monkeypatch, payload):
    calls = []
    monkeypatch.setattr(benchmark_deploy.os.path, "exists", lambda _p: True)

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return SimpleNamespace(
            returncode=0 if payload.get("ok") else 1,
            stdout=(json.dumps(payload) + "\n").encode("utf-8"),
            stderr=b"",
        )

    monkeypatch.setattr(benchmark_deploy.subprocess, "run", fake_run)
    return calls


@pytest.fixture
def mounted(tmp_path, monkeypatch):
    """Map the mounted Windows path the bridge verifies onto a temp dir."""
    root = tmp_path / "mnt"
    root.mkdir()
    seen = []

    def fake_mounted(path):
        seen.append(path)
        return str(root / os.path.basename(path.replace("\\", "/")))

    monkeypatch.setattr(benchmark_deploy, "_mounted_path", fake_mounted)
    return SimpleNamespace(root=root, seen=seen)


def test_export_via_windows_builds_argv_and_verifies_mounted_archive(monkeypatch, mounted):
    data = b"archived"
    (mounted.root / "base.Civ6Save").write_bytes(data)
    payload = {
        "ok": True,
        "save_name": BASE_SAVE,
        "source_path": r"C:\Docs\Single\x.Civ6Save",
        "dest_path": r"C:\arch\base.Civ6Save",
        "sha256": _sha(data),
        "size": len(data),
        "existed": False,
    }
    calls = _fake_bridge(monkeypatch, payload)

    result = benchmark_deploy.export_via_windows(
        "SEONDEOK 100 400 BC", "/mnt/c/arch/base.Civ6Save", expected_sha256=_sha(data)
    )

    assert result == payload
    [cmd] = calls
    assert cmd[2:] == [
        "export-save",
        "--name", "SEONDEOK 100 400 BC",
        "--destination", r"C:\arch\base.Civ6Save",
        "--json",
        "--sha256", _sha(data),
    ]
    assert mounted.seen == ["/mnt/c/arch/base.Civ6Save"]


def test_export_via_windows_accepts_windows_absolute_destination(monkeypatch, mounted):
    (mounted.root / "out.Civ6Save").write_bytes(b"x")
    calls = _fake_bridge(monkeypatch, {"ok": True, "sha256": _sha(b"x")})
    benchmark_deploy.export_via_windows("NAME", r"D:\arch\out.Civ6Save")
    [cmd] = calls
    assert "--sha256" not in cmd
    assert cmd[cmd.index("--destination") + 1] == r"D:\arch\out.Civ6Save"
    assert mounted.seen == [r"D:\arch\out.Civ6Save"]


def test_mounted_path_translates_windows_paths():
    assert benchmark_deploy._mounted_path(r"D:\arch\a b.Civ6Save") == "/mnt/d/arch/a b.Civ6Save"
    assert benchmark_deploy._mounted_path("/mnt/c/x/y.Civ6Save") == "/mnt/c/x/y.Civ6Save"


@pytest.mark.parametrize(
    "destination",
    [
        "/home/riz/projects/civ6-mcp/benchmarks/saves/base.Civ6Save",
        "benchmarks/saves/base.Civ6Save",
        r"arch\base.Civ6Save",
        "/mnt/c",
        "/mnt/cc/x.Civ6Save",
        r"\\wsl.localhost\Ubuntu\home\x.Civ6Save",
    ],
)
def test_export_via_windows_refuses_destinations_windows_cannot_reach(
    monkeypatch, destination
):
    calls = _fake_bridge(monkeypatch, {"ok": True, "sha256": "abc"})
    with pytest.raises(ValueError, match="Windows-visible absolute path"):
        benchmark_deploy.export_via_windows("NAME", destination)
    assert calls == []


def test_export_via_windows_raises_on_native_failure(monkeypatch, mounted):
    _fake_bridge(monkeypatch, {"ok": False, "error": "archive at X differs"})
    with pytest.raises(benchmark_deploy.DeploymentVerificationError, match="differs"):
        benchmark_deploy.export_via_windows("NAME", "/mnt/c/out.Civ6Save")


def test_export_via_windows_raises_when_native_digest_disagrees(monkeypatch, mounted):
    (mounted.root / "out.Civ6Save").write_bytes(b"x")
    _fake_bridge(monkeypatch, {"ok": True, "sha256": "other"})
    with pytest.raises(benchmark_deploy.DeploymentVerificationError, match="sha256"):
        benchmark_deploy.export_via_windows("NAME", "/mnt/c/out.Civ6Save", "abc")


def test_export_via_windows_raises_when_mounted_archive_disagrees(monkeypatch, mounted):
    (mounted.root / "out.Civ6Save").write_bytes(b"what WSL actually sees")
    _fake_bridge(monkeypatch, {"ok": True, "sha256": _sha(b"what Windows claims")})
    with pytest.raises(benchmark_deploy.DeploymentVerificationError, match="mounted"):
        benchmark_deploy.export_via_windows("NAME", "/mnt/c/out.Civ6Save")


def test_export_via_windows_raises_when_mounted_archive_missing(monkeypatch, mounted):
    _fake_bridge(monkeypatch, {"ok": True, "sha256": "abc"})
    with pytest.raises(benchmark_deploy.DeploymentVerificationError, match="mounted"):
        benchmark_deploy.export_via_windows("NAME", "/mnt/c/out.Civ6Save")


# --- WSL-side publish into the repo store ---------------------------------


@pytest.fixture
def store(tmp_path):
    src_dir = tmp_path / "windows_checkout"
    src_dir.mkdir()
    dest_dir = tmp_path / "benchmarks" / "saves"
    dest_dir.mkdir(parents=True)
    return SimpleNamespace(src=src_dir, dest=dest_dir)


def test_publish_archive_copy_creates_verified_copy(store):
    data = b"verified archive"
    source = store.src / "a.Civ6Save"
    source.write_bytes(data)
    dest = store.dest / "a.Civ6Save"

    result = benchmark_deploy.publish_archive_copy(
        str(source), str(dest), expected_sha256=_sha(data)
    )

    assert dest.read_bytes() == data
    assert result == {
        "source_path": str(source),
        "dest_path": str(dest),
        "sha256": _sha(data),
        "size": len(data),
        "existed": False,
    }
    assert [p.name for p in store.dest.iterdir()] == ["a.Civ6Save"]


def test_publish_archive_copy_is_idempotent_for_identical_archive(store):
    data = b"same"
    (store.src / "a.Civ6Save").write_bytes(data)
    dest = store.dest / "a.Civ6Save"
    dest.write_bytes(data)

    result = benchmark_deploy.publish_archive_copy(
        str(store.src / "a.Civ6Save"), str(dest), expected_sha256=_sha(data)
    )

    assert result["existed"] is True
    assert [p.name for p in store.dest.iterdir()] == ["a.Civ6Save"]


def test_publish_archive_copy_refuses_differing_archive(store):
    (store.src / "a.Civ6Save").write_bytes(b"new")
    dest = store.dest / "a.Civ6Save"
    dest.write_bytes(b"old")

    with pytest.raises(benchmark_deploy.DeploymentVerificationError, match="differs"):
        benchmark_deploy.publish_archive_copy(
            str(store.src / "a.Civ6Save"), str(dest), expected_sha256=_sha(b"new")
        )
    assert dest.read_bytes() == b"old"
    assert [p.name for p in store.dest.iterdir()] == ["a.Civ6Save"]


def test_publish_archive_copy_never_overwrites_a_racing_archive(store, monkeypatch):
    (store.src / "a.Civ6Save").write_bytes(b"ours")
    dest = store.dest / "a.Civ6Save"
    real_link = os.link

    def racing_link(src, dst):
        with open(dst, "wb") as fh:
            fh.write(b"theirs")
        return real_link(src, dst)

    monkeypatch.setattr(benchmark_deploy.os, "link", racing_link)
    monkeypatch.setattr(
        benchmark_deploy.os, "replace", lambda *a: pytest.fail("os.replace must not be used")
    )

    with pytest.raises(benchmark_deploy.DeploymentVerificationError, match="differs"):
        benchmark_deploy.publish_archive_copy(
            str(store.src / "a.Civ6Save"), str(dest), expected_sha256=_sha(b"ours")
        )
    assert dest.read_bytes() == b"theirs"
    assert [p.name for p in store.dest.iterdir()] == ["a.Civ6Save"]


def test_publish_archive_copy_rejects_source_digest_mismatch(store):
    (store.src / "a.Civ6Save").write_bytes(b"bytes")
    dest = store.dest / "a.Civ6Save"
    with pytest.raises(benchmark_deploy.DeploymentVerificationError, match="expected"):
        benchmark_deploy.publish_archive_copy(
            str(store.src / "a.Civ6Save"), str(dest), expected_sha256="0" * 64
        )
    assert list(store.dest.iterdir()) == []

