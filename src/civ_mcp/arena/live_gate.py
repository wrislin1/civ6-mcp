"""Generic coordinator-owned live-gate driver infrastructure.

Design: docs/superpowers/specs/2026-07-17-arena-live-gate-driver-design.md

Scenario-agnostic by contract: this module owns the immutable gate state,
the strict event reducer, write-ahead persistence (events.jsonl is
authoritative; state.json is an atomically replaced derived snapshot;
result.json appears only for restart_required / terminal states), and the
restart/terminal signal vocabulary. It must not import unofficial-channel
scenario details or Lua builders, and it never mutates canonical channel state.
"""

from __future__ import annotations

import json
import math
import os
import secrets
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

GATE_SCHEMA_VERSION = 1

GATE_ACTIVE = "active"
GATE_RESTART_REQUIRED = "restart_required"
GATE_FAILED = "failed"
GATE_PASSED = "passed"

_TERMINAL_STATUSES = frozenset({GATE_FAILED, GATE_PASSED})
# restart_required is a checkpoint, not terminal: the resumed process
# transitions it back to active via restart_verified.
_SIGNAL_STATUSES = frozenset({GATE_RESTART_REQUIRED, GATE_FAILED, GATE_PASSED})

GATE_EVENT_KINDS = frozenset(
    {
        "gate_initialized",
        "phase_advanced",
        "data_recorded",
        "observation_recorded",
        "action_planned",
        "action_verified",
        "privacy_asserted",
        "restart_required",
        "restart_verified",
        "gate_failed",
        "gate_passed",
    }
)

_ACTION_PLANNED_FIELDS = (
    "turn",
    "player_id",
    "phase",
    "name",
    "source_id",
    "payload_digest",
)
_PRIVACY_FIELDS = ("turn", "player_id", "artifact_kind", "input_digest", "result")
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)


class GateStateError(RuntimeError):
    """Invalid gate event, journal, snapshot, or identity."""


class _FrozenList(tuple):
    """Immutable list representation retaining ordinary-list equality."""

    def __eq__(self, other: object) -> bool:
        if isinstance(other, (list, tuple)):
            return tuple.__eq__(self, tuple(other))
        return NotImplemented

    __hash__ = tuple.__hash__


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise GateStateError(
                    f"unsupported gate mapping key type {type(key).__name__}"
                )
            frozen[key] = _freeze(item)
        return MappingProxyType(frozen)
    if isinstance(value, _FrozenList):
        return _FrozenList(_freeze(item) for item in value)
    if isinstance(value, list):
        return _FrozenList(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return _FrozenList(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        raise GateStateError(f"unsupported gate value type {type(value).__name__}")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise GateStateError(f"non-finite float is not supported: {value!r}")
        return value
    if value is None or isinstance(value, (bool, int, str)):
        return value
    raise GateStateError(f"unsupported gate value type {type(value).__name__}")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class GateEvent:
    schema_version: int
    sequence: int
    kind: str
    payload: dict

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", _freeze(self.payload))


@dataclass(frozen=True)
class GateState:
    schema_version: int
    run_id: str
    scenario: str
    scenario_revision: int
    roles: tuple[tuple[str, int], ...]
    config_fingerprint: dict
    phase: str
    status: str = GATE_ACTIVE
    reason: str = ""
    restart_count: int = 0
    pending_actions: tuple[dict, ...] = ()
    verified_actions: tuple[dict, ...] = ()
    observations: tuple[dict, ...] = ()
    privacy_assertions: tuple[dict, ...] = ()
    data: dict = field(default_factory=dict)
    last_event_sequence: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "roles",
            tuple((str(name), int(player_id)) for name, player_id in self.roles),
        )
        for name in ("config_fingerprint", "data"):
            object.__setattr__(self, name, _freeze(getattr(self, name)))
        for name in (
            "pending_actions",
            "verified_actions",
            "observations",
            "privacy_assertions",
        ):
            object.__setattr__(
                self,
                name,
                tuple(_freeze(item) for item in getattr(self, name)),
            )


def _normalized_roles(roles: Any) -> tuple[tuple[str, int], ...]:
    if not isinstance(roles, Mapping) or not roles:
        raise GateStateError(f"gate roles must be a non-empty mapping, got {roles!r}")
    try:
        return tuple(sorted((str(name), int(pid)) for name, pid in roles.items()))
    except (TypeError, ValueError) as exc:
        raise GateStateError(f"invalid gate roles {roles!r}") from exc


def reduce_gate_event(state: GateState | None, event: GateEvent) -> GateState:
    if event.schema_version != GATE_SCHEMA_VERSION:
        raise GateStateError(f"unknown gate event schema {event.schema_version!r}")
    if event.kind not in GATE_EVENT_KINDS:
        raise GateStateError(f"unknown gate event kind {event.kind!r}")
    payload = event.payload
    if not isinstance(payload, Mapping):
        raise GateStateError("gate event payload must be a mapping")

    if event.kind == "gate_initialized":
        if state is not None:
            raise GateStateError("gate_initialized must be the first event")
        if event.sequence != 1:
            raise GateStateError("gate_initialized must have sequence 1")
        try:
            return GateState(
                schema_version=GATE_SCHEMA_VERSION,
                run_id=str(payload["run_id"]),
                scenario=str(payload["scenario"]),
                scenario_revision=int(payload["scenario_revision"]),
                roles=_normalized_roles(payload["roles"]),
                config_fingerprint=payload["config_fingerprint"],
                phase=str(payload["phase"]),
                last_event_sequence=1,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GateStateError(f"invalid gate_initialized payload: {exc}") from exc

    if state is None:
        raise GateStateError("gate journal must begin with gate_initialized")
    if event.sequence != state.last_event_sequence + 1:
        raise GateStateError(
            f"gate event sequence {event.sequence} does not follow "
            f"{state.last_event_sequence}"
        )
    if state.status in _TERMINAL_STATUSES:
        raise GateStateError(f"gate is terminal ({state.status}); no further events")
    privacy_failed = any(
        assertion.get("result") == "FAIL" for assertion in state.privacy_assertions
    )
    if privacy_failed and event.kind != "gate_failed":
        raise GateStateError("a failed privacy assertion permits only gate_failed")

    changes: dict[str, Any] = {"last_event_sequence": event.sequence}
    kind = event.kind
    if kind == "phase_advanced":
        if state.pending_actions:
            raise GateStateError("cannot advance phase with unverified planned actions")
        if state.status == GATE_RESTART_REQUIRED:
            raise GateStateError("cannot advance phase before restart_verified")
        try:
            changes["phase"] = str(payload["phase"])
        except KeyError as exc:
            raise GateStateError("phase_advanced missing phase") from exc
    elif kind == "data_recorded":
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise GateStateError("data_recorded payload.data must be a mapping")
        changes["data"] = {**state.data, **data}
    elif kind == "observation_recorded":
        changes["observations"] = state.observations + (payload,)
    elif kind == "action_planned":
        missing = [name for name in _ACTION_PLANNED_FIELDS if name not in payload]
        if missing:
            raise GateStateError(f"action_planned missing field(s) {missing}")
        source_id = payload["source_id"]
        known = state.pending_actions + state.verified_actions
        if any(entry["source_id"] == source_id for entry in known):
            raise GateStateError(f"duplicate planned source {source_id!r}")
        changes["pending_actions"] = state.pending_actions + (payload,)
    elif kind == "action_verified":
        source_id = payload.get("source_id")
        matches = [
            entry for entry in state.pending_actions if entry["source_id"] == source_id
        ]
        if len(matches) != 1:
            raise GateStateError(f"action_verified for unplanned source {source_id!r}")
        changes["pending_actions"] = tuple(
            entry
            for entry in state.pending_actions
            if entry["source_id"] != source_id
        )
        changes["verified_actions"] = state.verified_actions + (payload,)
    elif kind == "privacy_asserted":
        missing = [name for name in _PRIVACY_FIELDS if name not in payload]
        if missing:
            raise GateStateError(f"privacy_asserted missing field(s) {missing}")
        if payload["result"] not in ("PASS", "FAIL"):
            raise GateStateError("privacy result must be PASS or FAIL")
        changes["privacy_assertions"] = state.privacy_assertions + (payload,)
    elif kind == "restart_required":
        if state.restart_count >= 1:
            raise GateStateError("a second restart request is not allowed")
        if state.pending_actions:
            raise GateStateError("cannot request restart with unverified planned actions")
        changes["status"] = GATE_RESTART_REQUIRED
        changes["restart_count"] = state.restart_count + 1
    elif kind == "restart_verified":
        if state.status != GATE_RESTART_REQUIRED:
            raise GateStateError("restart_verified requires restart_required status")
        changes["status"] = GATE_ACTIVE
    elif kind == "gate_failed":
        changes["status"] = GATE_FAILED
        changes["reason"] = str(payload.get("reason", ""))
    elif kind == "gate_passed":
        if state.status != GATE_ACTIVE:
            raise GateStateError("gate_passed requires an active gate")
        if state.pending_actions:
            raise GateStateError("cannot pass with unverified planned actions")
        changes["status"] = GATE_PASSED
    return replace(state, **changes)


# --- Private persistence (mirrors ChannelRuntime's ownership rules) ---------


def _open_directory_fd(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _CLOEXEC | _NOFOLLOW
    fd = -1
    try:
        fd = os.open(path, flags)
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode):
            raise GateStateError(f"gate path {path} is not a directory")
        return fd
    except GateStateError:
        if fd >= 0:
            os.close(fd)
        raise
    except OSError as exc:
        raise GateStateError(f"cannot open gate directory {path}: {exc}") from exc


def _ensure_private_directory(path: Path) -> None:
    try:
        if path.exists() or path.is_symlink():
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise GateStateError(f"gate path {path} is not a directory")
        else:
            path.mkdir(mode=0o700, parents=True)
        directory_fd = _open_directory_fd(path)
        try:
            os.fchmod(directory_fd, 0o700)
        finally:
            os.close(directory_fd)
    except GateStateError:
        raise
    except OSError as exc:
        raise GateStateError(f"cannot create private gate directory: {exc}") from exc


def _same_regular_file(directory_fd: int, name: str, descriptor_info: os.stat_result) -> None:
    try:
        path_info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except OSError as exc:
        raise GateStateError(f"gate file {name} changed during access: {exc}") from exc
    if (
        stat.S_ISLNK(path_info.st_mode)
        or not stat.S_ISREG(path_info.st_mode)
        or path_info.st_dev != descriptor_info.st_dev
        or path_info.st_ino != descriptor_info.st_ino
    ):
        raise GateStateError(f"gate file {name} is not a stable regular file")


def _require_optional_regular_file(path: Path) -> None:
    directory_fd = _open_directory_fd(path.parent)
    descriptor = -1
    try:
        try:
            descriptor = os.open(
                path.name,
                os.O_RDONLY | _CLOEXEC | _NOFOLLOW | _NONBLOCK,
                dir_fd=directory_fd,
            )
        except FileNotFoundError:
            return
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise GateStateError(f"gate file {path} is not a regular file")
        _same_regular_file(directory_fd, path.name, info)
        os.fchmod(descriptor, 0o600)
    except GateStateError:
        raise
    except OSError as exc:
        raise GateStateError(f"cannot validate private gate file {path}: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory_fd)


def _ensure_private_file(path: Path) -> None:
    directory_fd = _open_directory_fd(path.parent)
    descriptor = -1
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        descriptor = os.open(
            path.name,
            flags | _CLOEXEC | _NOFOLLOW | _NONBLOCK,
            0o600,
            dir_fd=directory_fd,
        )
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise GateStateError(f"gate file {path} is not a regular file")
        _same_regular_file(directory_fd, path.name, info)
        os.fchmod(descriptor, 0o600)
    except GateStateError:
        raise
    except OSError as exc:
        raise GateStateError(f"cannot create private gate file {path}: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory_fd)


def _read_private_bytes(path: Path) -> bytes:
    directory_fd = _open_directory_fd(path.parent)
    descriptor = -1
    try:
        descriptor = os.open(
            path.name,
            os.O_RDONLY | _CLOEXEC | _NOFOLLOW | _NONBLOCK,
            dir_fd=directory_fd,
        )
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise GateStateError(f"gate file {path} is not a regular file")
        _same_regular_file(directory_fd, path.name, info)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        _same_regular_file(directory_fd, path.name, info)
        return b"".join(chunks)
    except GateStateError:
        raise
    except OSError as exc:
        raise GateStateError(f"cannot read private gate file {path}: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory_fd)


def _private_regular_file_exists(path: Path) -> bool:
    directory_fd = _open_directory_fd(path.parent)
    descriptor = -1
    try:
        try:
            descriptor = os.open(
                path.name,
                os.O_RDONLY | _CLOEXEC | _NOFOLLOW | _NONBLOCK,
                dir_fd=directory_fd,
            )
        except FileNotFoundError:
            return False
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise GateStateError(f"gate file {path} is not a regular file")
        _same_regular_file(directory_fd, path.name, info)
        return True
    except GateStateError:
        raise
    except OSError as exc:
        raise GateStateError(f"cannot inspect private gate file {path}: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory_fd)


def _remove_matching_private_json(path: Path, expected: dict) -> bool:
    directory_fd = _open_directory_fd(path.parent)
    descriptor = -1
    try:
        try:
            descriptor = os.open(
                path.name,
                os.O_RDONLY | _CLOEXEC | _NOFOLLOW | _NONBLOCK,
                dir_fd=directory_fd,
            )
        except FileNotFoundError:
            return False
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise GateStateError(f"gate file {path} is not a regular file")
        _same_regular_file(directory_fd, path.name, info)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        try:
            actual = json.loads(b"".join(chunks).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise GateStateError(f"invalid stale gate result: {exc}") from exc
        if actual != expected:
            raise GateStateError("active gate has an unrelated stale result.json")
        _same_regular_file(directory_fd, path.name, info)
        os.unlink(path.name, dir_fd=directory_fd)
        os.fsync(directory_fd)
        return True
    except GateStateError:
        raise
    except OSError as exc:
        raise GateStateError(f"cannot remove stale gate result {path}: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory_fd)


def _append_private_bytes(path: Path, payload: bytes) -> None:
    directory_fd = _open_directory_fd(path.parent)
    descriptor = -1
    try:
        descriptor = os.open(
            path.name,
            os.O_WRONLY | os.O_APPEND | _CLOEXEC | _NOFOLLOW | _NONBLOCK,
            dir_fd=directory_fd,
        )
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise GateStateError(f"gate file {path} is not a regular file")
        _same_regular_file(directory_fd, path.name, info)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
        _same_regular_file(directory_fd, path.name, info)
    except GateStateError:
        raise
    except OSError as exc:
        raise GateStateError(f"cannot append private gate file {path}: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory_fd)


def _destination_is_regular_or_absent(directory_fd: int, name: str) -> None:
    try:
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise GateStateError(f"cannot inspect gate destination {name}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise GateStateError(f"gate destination {name} is not a regular file")


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        view = view[written:]


def _atomic_private_json(path: Path, payload: dict) -> None:
    encoded = json.dumps(
        _thaw(payload),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    directory_fd = _open_directory_fd(path.parent)
    temporary_name = ""
    descriptor = -1
    try:
        _destination_is_regular_or_absent(directory_fd, path.name)
        for _ in range(128):
            candidate = f".{path.name}.{secrets.token_hex(16)}.tmp"
            try:
                descriptor = os.open(
                    candidate,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | _CLOEXEC | _NOFOLLOW,
                    0o600,
                    dir_fd=directory_fd,
                )
            except FileExistsError:
                continue
            temporary_name = candidate
            break
        else:
            raise GateStateError(f"cannot allocate private temporary for {path.name}")

        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise GateStateError("gate atomic temporary is not a regular file")
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, encoded)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1

        _destination_is_regular_or_absent(directory_fd, path.name)
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        temporary_name = ""
        _require_replaced_regular_file(directory_fd, path.name)
        os.fsync(directory_fd)
    except GateStateError:
        raise
    except OSError as exc:
        raise GateStateError(f"cannot atomically write private gate file {path}: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        os.close(directory_fd)


def _require_replaced_regular_file(directory_fd: int, name: str) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | _CLOEXEC | _NOFOLLOW | _NONBLOCK,
            dir_fd=directory_fd,
        )
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise GateStateError(f"gate destination {name} is not a regular file")
        _same_regular_file(directory_fd, name, info)
        os.fchmod(descriptor, 0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _state_to_dict(state: GateState) -> dict:
    return {
        "schema_version": state.schema_version,
        "run_id": state.run_id,
        "scenario": state.scenario,
        "scenario_revision": state.scenario_revision,
        "roles": _thaw(state.roles),
        "config_fingerprint": _thaw(state.config_fingerprint),
        "phase": state.phase,
        "status": state.status,
        "reason": state.reason,
        "restart_count": state.restart_count,
        "pending_actions": _thaw(state.pending_actions),
        "verified_actions": _thaw(state.verified_actions),
        "observations": _thaw(state.observations),
        "privacy_assertions": _thaw(state.privacy_assertions),
        "data": _thaw(state.data),
        "last_event_sequence": state.last_event_sequence,
    }


def _result_payload(state: GateState, *, status: str | None = None) -> dict:
    return {
        "schema_version": GATE_SCHEMA_VERSION,
        "run_id": state.run_id,
        "scenario": state.scenario,
        "scenario_revision": state.scenario_revision,
        "status": state.status if status is None else status,
        "phase": state.phase,
        "reason": state.reason,
        "restart_count": state.restart_count,
    }


def _read_journal(path: Path) -> tuple[GateEvent, ...]:
    try:
        text = _read_private_bytes(path).decode("utf-8")
    except UnicodeError as exc:
        raise GateStateError(f"cannot read gate journal: {exc}") from exc
    events: list[GateEvent] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise GateStateError(f"invalid gate journal line {line_number}: {exc}") from exc
        if not isinstance(raw, dict):
            raise GateStateError(f"invalid gate journal line {line_number}: not a mapping")
        try:
            events.append(
                GateEvent(
                    schema_version=int(raw["schema_version"]),
                    sequence=int(raw["sequence"]),
                    kind=str(raw["kind"]),
                    payload=raw["payload"],
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GateStateError(f"invalid gate journal line {line_number}: {exc}") from exc
    return tuple(events)


class LiveGateJournal:
    """Sole writer for one run's gate journal, snapshot, and result."""

    state: GateState

    def __init__(self, gate_dir: Path, state: GateState) -> None:
        self.gate_dir = gate_dir
        self.events_path = gate_dir / "events.jsonl"
        self.state_path = gate_dir / "state.json"
        self.result_path = gate_dir / "result.json"
        self.state = state

    @classmethod
    def open(
        cls,
        run_dir: Path,
        *,
        run_id: str,
        scenario: str,
        scenario_revision: int,
        roles: dict[str, int],
        config_fingerprint: dict,
        initial_phase: str,
    ) -> "LiveGateJournal":
        gate_dir = Path(run_dir) / "live_gate"
        _ensure_private_directory(gate_dir)
        events_path = gate_dir / "events.jsonl"
        state_path = gate_dir / "state.json"
        result_path = gate_dir / "result.json"
        _ensure_private_file(events_path)
        _require_optional_regular_file(state_path)
        _require_optional_regular_file(result_path)
        events = _read_journal(events_path)

        if not events:
            if _private_regular_file_exists(result_path):
                raise GateStateError(
                    "result.json exists without an initialized gate journal"
                )
            journal = cls.__new__(cls)
            journal.gate_dir = gate_dir
            journal.events_path = events_path
            journal.state_path = state_path
            journal.result_path = result_path
            journal.state = None  # type: ignore[assignment]
            journal._append_event(
                "gate_initialized",
                {
                    "run_id": run_id,
                    "scenario": scenario,
                    "scenario_revision": scenario_revision,
                    "roles": dict(roles),
                    "config_fingerprint": config_fingerprint,
                    "phase": initial_phase,
                },
            )
            return journal

        state: GateState | None = None
        for event in events:
            state = reduce_gate_event(state, event)
        assert state is not None
        expected = (
            run_id,
            scenario,
            int(scenario_revision),
            _normalized_roles(dict(roles)),
        )
        actual = (state.run_id, state.scenario, state.scenario_revision, state.roles)
        if expected != actual:
            raise GateStateError(
                f"gate identity mismatch: journal has {actual}, configuration wants {expected}"
            )
        if state.config_fingerprint != config_fingerprint:
            raise GateStateError("gate configuration fingerprint mismatch on resume")

        if state_path.exists() or state_path.is_symlink():
            try:
                snapshot = json.loads(_read_private_bytes(state_path).decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise GateStateError(f"invalid gate snapshot: {exc}") from exc
            if not isinstance(snapshot, dict):
                raise GateStateError("invalid gate snapshot: not a mapping")
            try:
                snapshot_sequence = int(snapshot.get("last_event_sequence", -1))
            except (TypeError, ValueError) as exc:
                raise GateStateError(f"invalid gate snapshot sequence: {exc}") from exc
            if snapshot_sequence > state.last_event_sequence:
                raise GateStateError("gate snapshot is newer than the write-ahead journal")

        journal = cls(gate_dir, state)
        journal._write_snapshot()
        journal._reconcile_active_result()
        return journal

    def append(self, kind: str, payload: dict) -> GateEvent:
        return self._append_event(kind, payload)

    def _append_event(self, kind: str, payload: dict) -> GateEvent:
        sequence = 1 if self.state is None else self.state.last_event_sequence + 1
        event = GateEvent(GATE_SCHEMA_VERSION, sequence, kind, payload)
        new_state = reduce_gate_event(self.state, event)  # validate before persisting
        line = json.dumps(
            {
                "schema_version": event.schema_version,
                "sequence": event.sequence,
                "kind": event.kind,
                "payload": _thaw(event.payload),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        _append_private_bytes(self.events_path, line + b"\n")
        self.state = new_state
        self._write_snapshot()
        if kind == "restart_verified":
            self._reconcile_active_result()
        return event

    def _write_snapshot(self) -> None:
        if self.state is None:
            raise GateStateError("cannot snapshot an uninitialized gate")
        _atomic_private_json(self.state_path, _state_to_dict(self.state))

    def _reconcile_active_result(self) -> None:
        if self.state.status != GATE_ACTIVE:
            return
        if self.state.restart_count != 1:
            if _private_regular_file_exists(self.result_path):
                raise GateStateError("active gate has an unexpected result.json")
            return
        _remove_matching_private_json(
            self.result_path,
            _result_payload(self.state, status=GATE_RESTART_REQUIRED),
        )

    def write_result(self) -> None:
        if self.state is None or self.state.status not in _SIGNAL_STATUSES:
            status = None if self.state is None else self.state.status
            raise GateStateError(
                f"result.json is written only for {sorted(_SIGNAL_STATUSES)}, "
                f"not {status!r}"
            )
        _atomic_private_json(self.result_path, _result_payload(self.state))
