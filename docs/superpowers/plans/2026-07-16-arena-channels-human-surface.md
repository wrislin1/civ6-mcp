# Arena Channels Human Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let human seat 0 use private unofficial channels from an authenticated LAN-capable web page and provide an explicitly contaminating read-only analyst view without granting the web process canonical-state, game, or model authority.

**Architecture:** The coordinator remains the only `ChannelRuntime` writer. It publishes atomic typed projections and drains an append-only request queue with persisted cursor/idempotency; a separate FastAPI process reads only projections, appends validated records, and streams revisions. Human seat 0 is an explicit policy-free driver: the coordinator polls channel work while Civ 6 remains local to the person and never auto-plays or auto-ends that turn.

**Tech Stack:** Python 3.12, FastAPI/Starlette/Uvicorn already provided by `fastapi[standard]`, inline dependency-free HTML/CSS/JavaScript, JSONL `O_APPEND` queue with `fsync`, authenticated streaming `fetch`, pytest/pytest-asyncio and FastAPI `TestClient`.

**Spec:** Read `docs/superpowers/specs/2026-07-16-arena-channels-human-surface-design.md`, the approved core spec, and the approved game-master spec before Task 1.

## Global Constraints

- Begin only from the reviewed `arena-game-master` tip; deterministic `ChannelRuntime` remains the source of truth and only game-side-effect caller.
- The web process never imports/reads/writes `channels/state.json` or `channels/events.jsonl`, never imports `GameState`, never connects to FireTuner, and never invokes a model.
- Runtime outputs are exactly `channels/views/seat0.json`, `channels/views/seat0_view.md`, optional `channels/views/analyst.json`, and `channels/seat0_queue.jsonl`.
- Projection schema is exactly version 1 with run ID, monotonic revision, UTC `generated_at`, player ID, permanent `privacy_contaminated`, typed channels, UI metadata, and recent receipts.
- Queue records are exactly `player_action` (actor always 0) and server-authored `audit` (`analyst_accessed` only). Unknown types/events become rejected receipts.
- Queue writes are one compact JSON object plus newline, one `O_APPEND` `os.write`, then `fsync`; maximum encoded record is 8 KiB. Queue files remain append-only for the run.
- Cursor and applied request UUIDs persist in canonical channel state. Pure actions may apply on any poll; `fund_deal`/`respond_to_payment` defer until seat 0 is local and InGame-safe.
- Receipts are exactly `applied`, `deferred`, `rejected`, or `duplicate` and appear in the next seat-0 projection.
- Default bind is `127.0.0.1`. Any non-loopback bind fails without non-blank `CIV_MCP_CHANNELS_TOKEN`.
- Tokens use `Authorization: Bearer`, remain only in browser JavaScript memory, and never appear in URLs, HTML, projections, logs, or errors.
- CORS is disabled. Mutations require JSON and same host/origin checks. Inline strings use `textContent`; no external scripts/fonts/assets or rendered model HTML.
- Browser streams use authenticated `fetch`, not `EventSource`; poll atomic projection revisions about once per second and emit bounded heartbeats.
- Analyst shell/data do not exist unless explicitly enabled. First authenticated data access must append/fsync `analyst_accessed`; data is withheld until a newer runtime projection confirms contamination.
- Managed web requires player 0 `provider: human` with `channels.enabled: true`. Human provider has blank model, no policy/backend, and never appears in puppet IDs.
- Web/process failure never blocks coordinator polling, model turns, human Civ 6 actions, or cleanup. `BaseException` still propagates.
- Owner-only permissions remain `0o700` directories and `0o600` files; the web process never widens them.
- Run tests as `uv run pytest tests/ -q`.
- End state is an unmerged local branch `arena-channels-human-surface`, based on the reviewed master tip in a worktree created via `superpowers:using-git-worktrees`. Do not push or merge without riz's direction.

## Stable Interfaces

`src/civ_mcp/arena/channel_queue.py` exports:

```python
@dataclass(frozen=True)
class QueueEnvelope:
    schema_version: int
    record_type: Literal["player_action", "audit"]
    request_id: str
    submitted_at: str
    actor: int | None
    action: dict | None
    event: str | None

@dataclass(frozen=True)
class QueueItem:
    start_cursor: int
    end_cursor: int
    envelope: QueueEnvelope | None
    error: str

def append_player_action(queue_path: Path, action: dict,
                         request_id: str | None = None) -> QueueEnvelope: ...
def append_audit(queue_path: Path, event: Literal["analyst_accessed"],
                 request_id: str | None = None) -> QueueEnvelope: ...
def read_queue(queue_path: Path, cursor: int) -> tuple[QueueItem, ...]: ...
```

`src/civ_mcp/arena/channel_web.py` exports:

```python
@dataclass(frozen=True)
class WebSettings:
    run_dir: Path
    host: str = "127.0.0.1"
    port: int = 8765
    token: str = ""
    analyst_enabled: bool = False
    poll_interval_s: float = 1.0
    heartbeat_s: float = 15.0

def validate_web_settings(settings: WebSettings) -> None: ...
def create_app(settings: WebSettings) -> FastAPI: ...
def main(argv: list[str] | None = None) -> int: ...
```

The core runtime gains `drain_human_queue`, `publish_views`, and view/receipt fields. The master runtime's existing `latest_briefing(0)` and idempotent handoff call are optional inputs; human channels still work with master mode off.

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/civ_mcp/arena/channel_queue.py` | Create | Queue envelopes, UUID/time, one-write append/fsync, incremental complete-line reader |
| `src/civ_mcp/arena/channel_web.py` | Create | Settings, auth/security, projection store, SSE, pages, action/analyst endpoints, CLI |
| `src/civ_mcp/arena/channels.py` | Modify | UI/analyst projection DTOs, receipts, legal actions, contamination/master metadata |
| `src/civ_mcp/arena/channel_runtime.py` | Modify | Queue drain/defer/replay, receipts, atomic view publication |
| `src/civ_mcp/arena/config.py`, `src/civ_mcp/arena/experiment.py` | Modify | Human driver, managed web/analyst flags and validation |
| `src/civ_mcp/arena/coordinator.py` | Modify | Policy-free human turn polling, safe queue actions, master handoff, view refresh |
| `src/civ_mcp/arena/arena.py` | Modify | CLI flags, managed child startup/logging/termination |
| `pyproject.toml` | Modify | `civ-arena-channels` console script |
| `tests/arena/test_channel_queue.py` | Create | Append/read/partial/size/UUID/idempotency contracts |
| `tests/arena/test_channel_web.py` | Create | Auth, health, action, SSE, HTML/CSP, analyst audit/gate, CLI |
| `tests/arena/test_channel_runtime.py` | Modify | Queue drain/defer/receipts/projections/contamination/restart |
| Existing arena tests | Modify | Config/experiment/arena/coordinator/master handoff/process resilience |
| `experiments/arena-channels-human-smoke.yaml` | Create | Human seat 0 plus automated counterpart and active private master |
| `docs/superpowers/plans/2026-07-16-arena-channels-human-live-gate.md` | Create | Authenticated LAN round-trip/restart/outage evidence checklist |

## Dependency Order and Spec Coverage

Task 1 adds the explicit human driver. Task 2 builds the authority-neutral queue. Tasks 3–4 add coordinator-owned consumption and generated projections. Tasks 5–6 build the authenticated read/append web surface. Task 7 adds standalone/managed lifecycle. Task 8 wires human polling/master handoff. Tasks 9–10 complete resilience/security and the attended LAN gate.

| Approved design area | Task |
|---|---|
| Explicit human seat and managed validation | 1 |
| Queue types, size, append/fsync, cursor/UUID/replay | 2–3 |
| Pure/game-side defer and receipts | 3 |
| Seat-0/analyst projection boundaries and metadata | 4 |
| Auth/network/CSP/no-secret shell | 5–6 |
| SSE fetch/revision/heartbeat/reconnect | 6 |
| Standalone/enqueue/managed child lifecycle | 7 |
| Human local polling, one master handoff, no auto-end | 8 |
| Analyst audit/permanent contamination and outage isolation | 4–9 |
| Authenticated LAN/live round trip | 10 |

---

### Task 1: Explicit human driver and managed-surface configuration

**Files:**
- Modify: `src/civ_mcp/arena/config.py:25-220`
- Modify: `src/civ_mcp/arena/experiment.py:13-430`
- Modify: `src/civ_mcp/arena/arena.py:18-180`
- Modify: `tests/arena/test_config.py`
- Modify: `tests/arena/test_experiment.py`
- Modify: `tests/arena/test_arena_wiring.py`

**Interfaces:**
- Consumes: `PlayerSpec.driver_kind`, `resolved_puppet_ids`, strict experiment parser.
- Produces: provider `human`, `ArenaConfig.channels_web_port`, `ArenaConfig.channels_analyst`, `validate_channels_web_config`.

- [ ] **Step 1: Add failing human-driver and validation tests**

```python
def test_human_driver_has_no_model_policy_or_puppet_identity():
    human = PlayerSpec(0, "human", "", options=CivOptions(channels=ChannelOptions(True)))
    cfg = ArenaConfig(players=[human], channels_web_port=8765)
    assert human.driver_kind() == "human"
    assert resolved_puppet_ids(cfg) == []
    validate_channels_web_config(cfg)


@pytest.mark.parametrize("seat, match", [
    (PlayerSpec(0, "human", "", options=CivOptions()), "channels.enabled"),
    (PlayerSpec(0, "local", "m", options=CivOptions(channels=ChannelOptions(True))), "provider: human"),
    (PlayerSpec(1, "human", "", options=CivOptions(channels=ChannelOptions(True))), "player 0"),
])
def test_managed_web_requires_channel_enabled_human_seat_zero(seat, match):
    cfg = ArenaConfig(players=[seat], channels_web_port=8765)
    with pytest.raises(ValueError, match=match):
        validate_channels_web_config(cfg)


def test_human_yaml_requires_blank_model_and_rejects_local_knobs(tmp_path):
    good = write_experiment(tmp_path, {"civs": [{"player": 0, "provider": "human",
        "model": "", "channels": {"enabled": True}}]})
    assert load_experiment(good).players[0].driver_kind() == "human"
    bad = write_experiment(tmp_path, {"civs": [{"player": 0, "provider": "human",
        "model": "m", "channels": {"enabled": True}}]}, name="bad.yaml")
    with pytest.raises(ValueError, match="human model must be blank"):
        load_experiment(bad)
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `uv run pytest tests/arena/test_config.py tests/arena/test_experiment.py tests/arena/test_arena_wiring.py -q`

Expected: unknown provider/constructor fields and missing validation helper.

- [ ] **Step 3: Implement human driver and config validation**

Add `human` to valid providers. `PlayerSpec.driver_kind()` returns `human` first. Human requires player ID 0, blank model/gateway, no local-only knobs, `playbook: none`, `context_budget: auto`, `memory.enabled: false`, `task_tracker.enabled: false`, and `attention.mode: off`; deterministic `briefing` and `channels` are allowed because the master/handoff surface may consume them. `build_policies` skips human specs and never constructs backend/CLI policy.

Add:

```python
@dataclass
class ArenaConfig:
    # existing fields
    channels_web_port: int | None = None
    channels_analyst: bool = False


def validate_channels_web_config(config: ArenaConfig) -> None:
    if config.channels_web_port is None:
        if config.channels_analyst and not any(
            p.options.channels.enabled for p in config.players
        ):
            raise ValueError("channels_analyst requires at least one channel-enabled player")
        return
    if not 1 <= config.channels_web_port <= 65535:
        raise ValueError("channels_web_port must be 1..65535")
    seat0 = next((p for p in config.players if p.player_id == 0), None)
    if seat0 is None or seat0.driver_kind() != "human":
        raise ValueError("managed channels web requires player 0 provider: human")
    if not seat0.options.channels.enabled:
        raise ValueError("managed channels web requires player 0 channels.enabled: true")
```

- [ ] **Step 4: Add CLI flags without config-file ambiguity**

Add `--channels-web PORT` and `--channels-analyst`, and accept top-level YAML keys `channels_web_port` and `channels_analyst`. `channels_analyst: true` may publish the owner-only analyst projection for a standalone server even when no managed port is configured; a managed port still requires channel-enabled human seat 0. Like other config-owned flags, reject command-line override when a config owns an incompatible value; set resolved fields before `validate_arena_config`/`validate_channels_web_config`. This task validates only; process spawning is Task 7.

- [ ] **Step 5: Run GREEN and commit**

Run: `uv run pytest tests/arena/test_config.py tests/arena/test_experiment.py tests/arena/test_arena_wiring.py -q`

Expected: all human-driver/config tests pass.

```bash
git add src/civ_mcp/arena/config.py src/civ_mcp/arena/experiment.py src/civ_mcp/arena/arena.py tests/arena/test_config.py tests/arena/test_experiment.py tests/arena/test_arena_wiring.py
git commit -m "feat(arena): configure human channel seat"
```

### Task 2: Append-only typed queue and incremental reader

**Files:**
- Create: `src/civ_mcp/arena/channel_queue.py`
- Create: `tests/arena/test_channel_queue.py`

**Interfaces:**
- Consumes: `parse_channel_action` for later runtime validation; no canonical/game imports.
- Produces: Stable Interface queue types/functions.

- [ ] **Step 1: Write failing append/read/partial/size tests**

Create `tests/arena/test_channel_queue.py`:

```python
import json
import os
import uuid
import pytest

from civ_mcp.arena.channel_queue import append_audit, append_player_action, read_queue


def test_player_action_is_one_compact_line_actor_zero_and_owner_only(tmp_path, monkeypatch):
    path = tmp_path / "channels/seat0_queue.jsonl"
    writes = []
    real_write = os.write
    def recording_write(fd, data):
        writes.append(data)
        return real_write(fd, data)
    monkeypatch.setattr(os, "write", recording_write)
    record = append_player_action(path, {"action": "send_message", "to_player": 2, "text": "hi"},
        request_id="9c9544a8-2acd-4ea3-9af1-84a4b55f8e11")
    assert len(writes) == 1 and writes[0].endswith(b"\n") and writes[0].count(b"\n") == 1
    assert record.actor == 0 and record.record_type == "player_action"
    assert path.stat().st_mode & 0o777 == 0o600


def test_reader_stops_before_incomplete_tail_and_resumes(tmp_path):
    path = tmp_path / "q.jsonl"
    first = append_player_action(path, {"action": "fund_deal", "deal_id": "deal-000001"})
    full_cursor = path.stat().st_size
    with path.open("ab") as stream:
        stream.write(b'{"schema_version":1')
    items = read_queue(path, 0)
    assert len(items) == 1 and items[0].end_cursor == full_cursor


def test_audit_has_no_actor_and_unknown_audit_is_rejected_at_append(tmp_path):
    record = append_audit(tmp_path / "q.jsonl", "analyst_accessed")
    assert record.actor is None and record.event == "analyst_accessed"
    with pytest.raises(ValueError, match="unknown audit event"):
        append_audit(tmp_path / "q.jsonl", "become_admin")


def test_encoded_record_must_fit_8kib(tmp_path):
    with pytest.raises(ValueError, match="exceeds 8192 bytes"):
        append_player_action(tmp_path / "q.jsonl", {
            "action": "send_message", "to_player": 2, "text": "x" * 9000})
```

- [ ] **Step 2: Run new tests and confirm RED**

Run: `uv run pytest tests/arena/test_channel_queue.py -q`

Expected: import failure because `channel_queue.py` does not exist.

- [ ] **Step 3: Implement strict envelopes and one-write append**

Parse UUIDs with `uuid.UUID`; normalize to canonical string. Use UTC `datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")`. Reject unknown top-level fields/types on read. Append:

```python
def _append(queue_path: Path, envelope: QueueEnvelope) -> None:
    queue_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    encoded = (json.dumps(envelope_to_dict(envelope), sort_keys=True,
                          separators=(",", ":")) + "\n").encode("utf-8")
    if len(encoded) > 8 * 1024:
        raise ValueError(f"queue record exceeds 8192 bytes: {len(encoded)}")
    fd = os.open(queue_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        written = os.write(fd, encoded)
        if written != len(encoded):
            raise OSError(f"partial queue append: {written}/{len(encoded)} bytes")
        os.fsync(fd)
    finally:
        os.close(fd)
```

- [ ] **Step 4: Implement complete-line incremental reads**

`read_queue(path, cursor)` validates non-negative cursor, seeks, reads bytes, returns only newline-terminated records, and assigns byte start/end cursors. UTF-8/JSON/schema errors produce `QueueItem(envelope=None,error=...)` while retaining the exact end cursor. An incomplete tail produces no item and no cursor advance. Missing file returns `()`.

- [ ] **Step 5: Run GREEN and commit**

Run: `uv run pytest tests/arena/test_channel_queue.py -q`

Expected: all queue tests pass.

```bash
git add src/civ_mcp/arena/channel_queue.py tests/arena/test_channel_queue.py
git commit -m "feat(arena): add append-only human channel queue"
```

### Task 3: Coordinator-owned queue drain, deferral, idempotency, and receipts

**Files:**
- Modify: `src/civ_mcp/arena/channels.py`
- Modify: `src/civ_mcp/arena/channel_runtime.py`
- Modify: `tests/arena/test_channel_runtime.py`

**Interfaces:**
- Consumes: Task 2 queue items and core staged action application.
- Produces: `ChannelReceipt`, persisted cursor/request IDs, `drain_human_queue`.

- [ ] **Step 1: Add failing drain/replay/defer tests**

```python
@pytest.mark.asyncio
async def test_pure_queue_actions_apply_on_any_poll_and_advance_cursor(tmp_path, fake_gs):
    rt = runtime_with_human(tmp_path)
    record = append_player_action(rt.queue_path, {"action": "send_message", "to_player": 2, "text": "hi"})
    receipts = await rt.drain_human_queue(fake_gs, turn=8, local_player_id=2, ingame_safe=False)
    assert receipts[-1].status == "applied"
    assert rt.state.messages[-1].from_player == 0
    assert record.request_id in rt.state.applied_request_ids
    assert rt.state.queue_cursor == rt.queue_path.stat().st_size


@pytest.mark.asyncio
async def test_payment_action_defers_until_human_local_and_safe(tmp_path, payment_gs):
    rt, deal = await human_payment_due(tmp_path, payment_gs)
    record = append_player_action(rt.queue_path, {"action": "fund_deal", "deal_id": deal.id})
    first = await rt.drain_human_queue(payment_gs, turn=8, local_player_id=2, ingame_safe=True)
    assert first[-1].status == "deferred"
    assert record.request_id not in rt.state.applied_request_ids
    second = await rt.drain_human_queue(payment_gs, turn=8, local_player_id=0, ingame_safe=True)
    assert second[-1].status == "applied"
    assert payment_gs.offer_calls == 1


@pytest.mark.asyncio
async def test_replay_after_application_before_cursor_is_duplicate(tmp_path, fake_gs):
    rt = runtime_with_human(tmp_path)
    record = append_player_action(rt.queue_path, {"action": "send_message", "to_player": 2, "text": "once"})
    item = read_queue(rt.queue_path, 0)[0]
    await rt._apply_queue_item(fake_gs, item, turn=8, local_player_id=0, ingame_safe=True)
    assert rt.state.queue_cursor == 0
    assert record.request_id in rt.state.applied_request_ids
    reopened = reopen_runtime(tmp_path)
    receipts = await reopened.drain_human_queue(fake_gs, 8, 0, True)
    assert receipts[-1].status == "duplicate"
    assert sum(m.text == "once" for m in reopened.state.messages) == 1
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `uv run pytest tests/arena/test_channel_runtime.py -q`

Expected: missing queue drain/receipt state.

- [ ] **Step 3: Add receipt and queue state events**

Define frozen `ChannelReceipt(request_id, submitted_at, processed_at, status, reason, source_id)` and store the newest 50 in state. Add reducer events `queue_request_applied`, `queue_request_deferred`, `queue_request_rejected`, `queue_request_duplicate`, `queue_cursor_reserved`, and `queue_cursor_advanced`. Applied/duplicate/rejected advance the cursor. The first deferred encounter stores one receipt plus `queue_reservation={request_id,start_cursor,end_cursor}` and leaves the cursor at that line; later polls reuse the reservation without appending repeated deferred receipts. Successful/rejected completion advances the cursor and clears the reservation.

- [ ] **Step 4: Implement ordered drain rules**

For each complete item: malformed envelope → rejected/advance; duplicate UUID → duplicate/advance; audit event → Task 4 handler; player action actor must be 0 and parsed with bound actor 0. Pure action calls existing `apply_staged`. Payment action defers unless `local_player_id == 0 and ingame_safe`; once safe it uses core payment intent/result recovery. `_apply_queue_item` persists request ID/result without moving the byte cursor; `drain_human_queue` then appends the cursor-advance event. A crash between those events therefore re-reads the UUID as duplicate and advances without a second side effect. Stop after one reserved/deferred record; never overtake it.

- [ ] **Step 5: Run GREEN and commit**

Run: `uv run pytest tests/arena/test_channel_runtime.py tests/arena/test_channel_queue.py -q`

Expected: drain/defer/replay/receipt tests pass.

```bash
git add src/civ_mcp/arena/channels.py src/civ_mcp/arena/channel_runtime.py tests/arena/test_channel_runtime.py
git commit -m "feat(arena): drain human channel requests safely"
```

### Task 4: Atomic seat-0/analyst projections and contamination audit

**Files:**
- Modify: `src/civ_mcp/arena/channels.py`
- Modify: `src/civ_mcp/arena/channel_runtime.py`
- Modify: `tests/arena/test_channels.py`
- Modify: `tests/arena/test_channel_runtime.py`

**Interfaces:**
- Consumes: core projections/term registry, receipts, optional master metadata.
- Produces: `ChannelUiContext`, `build_seat0_view`, `build_analyst_view`, `publish_views`, audit contamination.

- [ ] **Step 1: Add failing projection/privacy/audit tests**

```python
def test_seat0_view_contains_only_seat0_scope_and_registry_metadata(tmp_path):
    rt = runtime_with_three_player_canaries(tmp_path)
    rt.publish_views(ChannelUiContext(master_mode="private_adviser",
        latest_master_briefing="PRIVATE ADVICE", analyst_enabled=False))
    data = json.loads((tmp_path / "channels/views/seat0.json").read_text())
    text = json.dumps(data)
    assert data["schema_version"] == 1 and data["player_id"] == 0
    assert "seat0-canary" in text and "player1-player2-secret" not in text
    assert data["ui"]["term_schemas"]
    assert data["ui"]["latest_master_briefing"] == "PRIVATE ADVICE"
    assert not (tmp_path / "channels/views/analyst.json").exists()


@pytest.mark.asyncio
async def test_analyst_audit_marks_permanent_contamination_and_publishes_full_scope(tmp_path, fake_gs):
    rt = runtime_with_three_player_canaries(tmp_path, analyst_enabled=True)
    append_audit(rt.queue_path, "analyst_accessed", request_id="2de0a816-c803-445f-9865-47fb1379284e")
    await rt.drain_human_queue(fake_gs, turn=9, local_player_id=2, ingame_safe=False)
    rt.publish_views(ChannelUiContext(master_mode="private_adviser",
        latest_master_briefing="", analyst_enabled=True))
    analyst = json.loads((tmp_path / "channels/views/analyst.json").read_text())
    assert analyst["privacy_contaminated"] is True
    assert "player1-player2-secret" in json.dumps(analyst)
    reopened = reopen_runtime(tmp_path)
    assert reopened.state.privacy_contaminated is True
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `uv run pytest tests/arena/test_channels.py tests/arena/test_channel_runtime.py -q`

Expected: view DTO/publication/audit behavior is absent.

- [ ] **Step 3: Implement typed UI projections and legal-action metadata**

`seat0.json` wraps `project_for_player(0)` plus term schemas from `TERM_REGISTRY`, enabled counterparties, normalized bounds, legal current actions derived from canonical deal states, master mode/latest seat-0 briefing, and newest receipts. `seat0_view.md` renders the same projection with escaped/plain text. `analyst.json` uses `project_all` plus deterministic evidence, master ruling/cost metadata supplied through `ChannelUiContext`; it is written only when `analyst_enabled`.

- [ ] **Step 4: Implement monotonic atomic publication and audit event**

Compute a hash of canonical event sequence plus serialized UI context. Publish only when hash changes: append a `projection_revision_advanced` event with next revision/hash, then atomically write JSON/Markdown using owner-only temp files, fsync, replace, and directory fsync. Every JSON has UTC `generated_at` ending `Z`.

Queue audit `analyst_accessed` calls `mark_privacy_contaminated("analyst_access", request_id)`, applies the request ID/cursor, and creates an applied receipt. Unknown audit events reject/advance. Contamination is permanent.

- [ ] **Step 5: Run GREEN and commit**

Run: `uv run pytest tests/arena/test_channels.py tests/arena/test_channel_runtime.py -q`

Expected: projection/privacy/audit/restart tests pass.

```bash
git add src/civ_mcp/arena/channels.py src/civ_mcp/arena/channel_runtime.py tests/arena/test_channels.py tests/arena/test_channel_runtime.py
git commit -m "feat(arena): publish private channel projections"
```

### Task 5: FastAPI settings, projection store, authentication, and health

**Files:**
- Create: `src/civ_mcp/arena/channel_web.py`
- Create: `tests/arena/test_channel_web.py`

**Interfaces:**
- Consumes: generated view paths and Task 2 append function only.
- Produces: `WebSettings`, `validate_web_settings`, `ProjectionStore`, `create_app`, data-free shells, `/healthz`, auth dependency.

- [ ] **Step 1: Write failing bind/auth/health/authority tests**

Create `tests/arena/test_channel_web.py`:

```python
from fastapi.testclient import TestClient

from civ_mcp.arena.channel_web import WebSettings, create_app, validate_web_settings


def test_loopback_allows_blank_token_but_nonloopback_requires_token(tmp_path):
    validate_web_settings(WebSettings(tmp_path, host="127.0.0.1", token=""))
    with pytest.raises(ValueError, match="CIV_MCP_CHANNELS_TOKEN"):
        validate_web_settings(WebSettings(tmp_path, host="0.0.0.0", token=""))
    validate_web_settings(WebSettings(tmp_path, host="0.0.0.0", token="secret"))


def test_private_endpoint_requires_bearer_when_configured(tmp_path):
    publish_seat0(tmp_path, revision=1)
    client = TestClient(create_app(WebSettings(tmp_path, token="secret", poll_interval_s=0.01)))
    assert client.get("/events").status_code == 401
    assert client.get("/events", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_health_has_no_private_projection_contents(tmp_path):
    publish_seat0(tmp_path, revision=3, message="HEALTH-CANARY")
    response = TestClient(create_app(WebSettings(tmp_path))).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "seat0_projection": "available", "revision": 3,
        "analyst_enabled": False}
    assert "HEALTH-CANARY" not in response.text


def test_web_module_has_no_canonical_game_or_model_imports():
    source = Path(channel_web.__file__).read_text()
    for forbidden in ["GameState", "events.jsonl", "state.json", "OpenAICompatBackend", "FireTuner"]:
        assert forbidden not in source
```

- [ ] **Step 2: Run new tests and confirm RED**

Run: `uv run pytest tests/arena/test_channel_web.py -q`

Expected: import failure because `channel_web.py` does not exist.

- [ ] **Step 3: Implement settings, safe projection reads, and auth**

Use `ipaddress.ip_address(host)` plus explicit `localhost` to recognize loopback. For configured token, parse exact `Bearer ` prefix and compare with `hmac.compare_digest`; never echo credentials. `ProjectionStore.read_seat0/read_analyst` reads only `channels/views/*.json`, requires dict/schema 1/run/revision, and returns unavailable on read/JSON/schema error; it has no canonical fallback.

- [ ] **Step 4: Implement app shell and health/CSP headers**

`GET /` returns a constant data-free `SEAT0_HTML` and no projection/token. `GET /analyst` returns 404 unless enabled, otherwise constant data-free shell. `GET /healthz` exposes process/projection availability/revision/analyst flag only. Add headers:

```python
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
}
```

Do not add CORS middleware.

- [ ] **Step 5: Run GREEN and commit**

Run: `uv run pytest tests/arena/test_channel_web.py -q`

Expected: settings/auth/health/authority tests pass.

```bash
git add src/civ_mcp/arena/channel_web.py tests/arena/test_channel_web.py
git commit -m "feat(arena): add secure channel web process"
```

### Task 6: Action endpoint, authenticated SSE, safe inline pages, and analyst gate

**Files:**
- Modify: `src/civ_mcp/arena/channel_web.py`
- Modify: `tests/arena/test_channel_web.py`

**Interfaces:**
- Consumes: `append_player_action`, `append_audit`, `ProjectionStore`.
- Produces: `/actions`, `/events`, `/analyst/events`, complete inline JS client.

- [ ] **Step 1: Add failing action/security/SSE/analyst tests**

```python
def test_actions_require_json_same_origin_and_actor_is_server_bound(tmp_path):
    publish_seat0(tmp_path, revision=1)
    client = TestClient(create_app(WebSettings(tmp_path)))
    assert client.post("/actions", data="x").status_code == 415
    assert client.post("/actions", json={"actor": 7, "action": "send_message",
        "to_player": 2, "text": "x"}).status_code == 422
    response = client.post("/actions", json={"action": "send_message", "to_player": 2,
        "text": "<img src=x onerror=alert(1)>"}, headers={"Origin": "http://testserver"})
    assert response.status_code == 202
    line = json.loads((tmp_path / "channels/seat0_queue.jsonl").read_text())
    assert line["actor"] == 0


def test_seat0_stream_emits_only_newer_revision_and_heartbeat(tmp_path):
    publish_seat0(tmp_path, revision=2, message="safe")
    client = TestClient(create_app(WebSettings(tmp_path, poll_interval_s=0.01, heartbeat_s=0.02)))
    with client.stream("GET", "/events", params={"after": 1}) as response:
        first = next(response.iter_lines())
        assert first == "event: snapshot"
        payload = next(line for line in response.iter_lines() if line.startswith("data: "))
        assert json.loads(payload[6:])["revision"] == 2


def test_shell_uses_textcontent_fetch_authorization_and_no_external_assets(tmp_path):
    html = TestClient(create_app(WebSettings(tmp_path, token="secret"))).get("/").text
    assert "textContent" in html and "fetch(" in html and "Authorization" in html
    assert "EventSource" not in html and "innerHTML" not in html
    assert "https://" not in html and "http://" not in html
    assert "secret" not in html


def test_first_analyst_data_access_audits_then_waits_for_contamination(tmp_path):
    publish_analyst(tmp_path, revision=2, contaminated=False, secret="ANALYST-SECRET")
    app = create_app(WebSettings(tmp_path, analyst_enabled=True, poll_interval_s=0.01))
    client = TestClient(app)
    with client.stream("GET", "/analyst/events", params={"after": 0}) as response:
        queue = (tmp_path / "channels/seat0_queue.jsonl").read_text()
        assert '"event":"analyst_accessed"' in queue
        assert "ANALYST-SECRET" not in response.text
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `uv run pytest tests/arena/test_channel_web.py -q`

Expected: endpoints/stream/client behavior is absent.

- [ ] **Step 3: Implement POST validation and one-record enqueue**

Require `Content-Type: application/json`; if `Origin` exists, parse it and require its netloc equals the request `Host`; reject forwarded cross-origin values. Body must be an object no larger than the request/server bound, optional `request_id`, and one core action with no actor. Validate shape through `parse_channel_action` using legal counterpart/rule metadata from the latest seat-0 projection, then pass the raw normalized action to `append_player_action`. Return 202 with request ID/status `queued`; never wait for coordinator application. Audit records cannot be submitted.

- [ ] **Step 4: Implement streaming fetch protocol and analyst audit barrier**

`GET /events?after=N` authenticates, then `StreamingResponse` emits:

```text
event: snapshot
id: 2
data: {compact projection JSON}

```

only for revision `> N`; heartbeat is `: heartbeat <utc>\n\n`. On projection unavailable, emit bounded `event: unavailable` without private data and continue polling.

`/analyst/events` first acquires an app-local async lock and appends/fsyncs one `analyst_accessed` record for that process before any analyst read. If append fails, return 503. Then stream no analyst data until a projection with `privacy_contaminated: true` and revision newer than the pre-audit revision exists.

- [ ] **Step 5: Implement complete safe page client and run GREEN**

The constant inline script stores token only in a closure variable, prompts on first private fetch, re-prompts after 401, uses `fetch` with bearer header and `ReadableStream` parsing, and clears rendered nodes with `replaceChildren()`. Create DOM nodes with `document.createElement`; set all model/user strings via `textContent`. Build forms solely from projection `ui.term_schemas`, `ui.legal_actions`, counterparties, and bounds. Show messages/deals/payment/grievances/master briefing/receipts; analyst page is read-only and has no POST controls.

Run: `uv run pytest tests/arena/test_channel_web.py -q`

Expected: action/SSE/HTML/analyst tests pass.

```bash
git add src/civ_mcp/arena/channel_web.py tests/arena/test_channel_web.py
git commit -m "feat(arena): stream authenticated channel controls"
```

### Task 7: Standalone/enqueue CLI and managed child lifecycle

**Files:**
- Modify: `src/civ_mcp/arena/channel_web.py`
- Modify: `src/civ_mcp/arena/arena.py:47-228`
- Modify: `pyproject.toml:81-85`
- Modify: `tests/arena/test_channel_web.py`
- Modify: `tests/arena/test_arena_wiring.py`

**Interfaces:**
- Consumes: `WebSettings/create_app`, queue append, validated managed config.
- Produces: `civ-arena-channels`, `enqueue` subcommand, managed child start/stop.

- [ ] **Step 1: Add failing CLI and process-lifecycle tests**

```python
def test_enqueue_cli_uses_shared_queue_code(tmp_path):
    rc = channel_web.main(["enqueue", "--run-dir", str(tmp_path), "--json",
        '{"action":"send_message","to_player":2,"text":"hello"}'])
    assert rc == 0
    record = json.loads((tmp_path / "channels/seat0_queue.jsonl").read_text())
    assert record["record_type"] == "player_action" and record["actor"] == 0


@pytest.mark.asyncio
async def test_managed_child_uses_run_dir_logs_and_is_terminated(monkeypatch, tmp_path):
    process = FakeProcess()
    spawn = capture_subprocess(monkeypatch, process)
    await run_arena_command(managed_web_args(tmp_path, port=8765, analyst=True))
    assert spawn.argv[:5] == ["civ-arena-channels", "--run-dir", str(tmp_path / "run-a"),
        "--host", "127.0.0.1"]
    assert "--analyst" in spawn.argv
    assert process.terminate_calls == 1 and process.wait_calls == 1
    assert (tmp_path / "run-a/channels/web.stdout.log").exists()


@pytest.mark.asyncio
async def test_arena_never_terminates_standalone_server(tmp_path):
    await run_arena_command(no_managed_web_args(tmp_path))
    assert FakeProcessRegistry.terminated == []
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `uv run pytest tests/arena/test_channel_web.py tests/arena/test_arena_wiring.py -q`

Expected: CLI entry/subcommand and child lifecycle are missing.

- [ ] **Step 3: Implement standalone and enqueue CLI**

Use argparse subcommands. Serve form is:

```text
civ-arena-channels --run-dir PATH --host 127.0.0.1 --port 8765 [--analyst]
```

It reads token only from `CIV_MCP_CHANNELS_TOKEN`, validates settings, and calls `uvicorn.run(app, host=..., port=..., access_log=False)`. `enqueue --run-dir PATH --json ACTION [--request-id UUID]` parses/validates JSON object and appends via `append_player_action`; it never opens projections/canonical state. Add `civ-arena-channels = "civ_mcp.arena.channel_web:main"` to project scripts.

- [ ] **Step 4: Implement managed child lifecycle**

After run directory creation and before connecting to Civ 6, open owner-only `channels/web.stdout.log` and `web.stderr.log`; spawn `civ-arena-channels --run-dir <run_dir> --host 127.0.0.1 --port <port>` plus `--analyst` when enabled. Save only the returned process handle. In `_run` `finally`, terminate/wait that handle with a 5-second bound, then kill only that child if necessary; never discover/kill by name or port.

- [ ] **Step 5: Run GREEN and commit**

Run: `uv run pytest tests/arena/test_channel_web.py tests/arena/test_arena_wiring.py -q`

Expected: CLI and managed/standalone lifecycle tests pass.

```bash
git add src/civ_mcp/arena/channel_web.py src/civ_mcp/arena/arena.py pyproject.toml tests/arena/test_channel_web.py tests/arena/test_arena_wiring.py
git commit -m "feat(arena): run standalone or managed channel web"
```

### Task 8: Policy-free human turn polling, safe payments, master handoff, and view refresh

**Files:**
- Modify: `src/civ_mcp/arena/coordinator.py:405-2060`
- Modify: `tests/arena/test_coordinator.py`

**Interfaces:**
- Consumes: human `PlayerSpec`, `drain_human_queue`, `publish_views`, master `invoke_master_for_handoff`.
- Produces: human-local coordinator branch that never invokes policy/end-turn and records one terminal human turn.

- [ ] **Step 1: Add failing human-turn integration tests**

```python
@pytest.mark.asyncio
async def test_human_turn_polls_queue_without_policy_or_end_request(monkeypatch):
    rt = FakeChannelRuntime()
    conn = human_local_then_advanced_conn(player=0, turn=8)
    result = await run_arena(conn, FakeGS(), human_config(), policy_for=lambda pid: fail("policy"),
        channel_runtime=rt)
    assert rt.drain_calls >= 1 and rt.publish_calls >= 1
    assert conn.end_turn_requests == 0
    assert result["turns"] == 1


@pytest.mark.asyncio
async def test_human_handoff_invokes_master_once_before_first_projection():
    order = []
    master = IdempotentFakeMaster(order)
    rt = FakeChannelRuntime(order=order)
    await run_arena(human_polling_conn(repeats=5), FakeGS(), human_master_config(),
        master_runtime=master, channel_runtime=rt)
    assert master.calls == 1
    assert order.index("master") < order.index("publish")


@pytest.mark.asyncio
async def test_game_side_queue_action_runs_only_when_seat0_local_and_ingame_safe():
    rt = FakeChannelRuntime(deferred_payment=True)
    await run_arena(nonlocal_then_human_conn(), FakeGS(), human_config(), channel_runtime=rt)
    assert rt.safe_flags == [False, True]


@pytest.mark.asyncio
async def test_web_projection_failure_does_not_stop_human_polling():
    rt = FakeChannelRuntime(publish_error=OSError("disk"))
    result = await run_arena(human_local_then_advanced_conn(), FakeGS(), human_config(), channel_runtime=rt)
    assert result["turns"] == 1
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `uv run pytest tests/arena/test_coordinator.py -q`

Expected: human has no policy, existing seat-0 auto branch fails/tries to invoke one.

- [ ] **Step 3: Add the human-local coordinator branch**

Before automated seat-0 piloting, detect configured seat 0 `driver_kind() == "human"`. On first `(player,turn)` local poll: admit channels, build deterministic briefing, invoke master with `handoff=True` once if active, and publish views. On every poll: drain queue with `ingame_safe=(st.local == 0 and st.active and conn.is_connected)`, tick deadlines/observations, republish only on changed source hash. Do not disconnect the tuner, call a policy, request end turn, skip units, or auto-resolve blockers.

When hook state advances away from `(0,turn)`, write exactly one transcript record with `driver="human"`, `turn_kind="human"`, channel receipts/errors, master handoff key, and terminal state `advanced`. On interruption, existing restore/disable cleanup remains authoritative.

- [ ] **Step 4: Poll channels for nonlocal turns and contain projection errors**

Call queue drain on other polls with `ingame_safe=False` so pure messages/deals apply but payment lines defer in order. Wrap drain/publication in `Exception` guards that log run-local errors and continue. Pass master latest briefing/contamination into `ChannelUiContext`; master off yields empty fields.

- [ ] **Step 5: Run GREEN and commit**

Run: `uv run pytest tests/arena/test_coordinator.py tests/arena/test_transcript.py tests/arena/test_master_runtime.py -q`

Expected: human polling/master/view/payment-safety tests pass and existing automated seat behavior stays green.

```bash
git add src/civ_mcp/arena/coordinator.py tests/arena/test_coordinator.py
git commit -m "feat(arena): coordinate human channel turns"
```

### Task 9: Security/recovery/outage integration matrix

**Files:**
- Modify: `tests/arena/test_channel_web.py`
- Modify: `tests/arena/test_channel_queue.py`
- Modify: `tests/arena/test_channel_runtime.py`
- Modify: `tests/arena/test_coordinator.py`

**Interfaces:**
- Consumes: complete human surface.
- Produces: exhaustive offline proof of authority boundary, recovery, escaping, and non-blocking failure.

- [ ] **Step 1: Add the complete security/recovery matrix**

Add parameterized tests for: missing/corrupt projection unavailable response; loopback aliases/IPv6; blank/invalid bearer; token absence from logs/HTML/errors; wrong content type; cross-origin mutation; oversized action/request; supplied actor/audit; malformed/incomplete queue; duplicate UUID; cursor restart; deferred payment restart; receipts; SSE after/reconnect/heartbeat; analyst disabled routes/files; audit append failure; contamination barrier; `<script>`, HTML attribute, newline, Unicode and JSON canaries; restrictive CSP; no CORS headers; standalone restart; managed child crash; and web process stopped while fake arena hook advances.

Representative outage test:

```python
@pytest.mark.asyncio
async def test_web_outage_leaves_fake_arena_progressing(tmp_path):
    rt = runtime_with_human(tmp_path)
    web = await start_test_web(rt.run_dir)
    await stop_test_web(web)
    result = await run_arena(human_local_then_advanced_conn(), FakeGS(), human_config(),
        channel_runtime=rt)
    assert result["turns"] == 1
    assert rt.state.last_event_sequence > 0
```

- [ ] **Step 2: Run the matrix and confirm any new RED cases**

Run: `uv run pytest tests/arena/test_channel_web.py tests/arena/test_channel_queue.py tests/arena/test_channel_runtime.py tests/arena/test_coordinator.py -q`

Expected: every newly exposed edge fails for a specific assertion before its narrow correction.

- [ ] **Step 3: Apply only the narrow corrections required by failing cases**

Keep corrections inside the owning module: parsing/append in `channel_queue.py`, HTTP/auth/stream in `channel_web.py`, cursor/receipt/projection in `channel_runtime.py`, and gameplay continuity in `coordinator.py`. Do not add canonical access or a web-to-runtime in-process shortcut.

- [ ] **Step 4: Re-run focused and full offline suites**

Run:

```bash
uv run pytest tests/arena/test_channel_web.py tests/arena/test_channel_queue.py tests/arena/test_channel_runtime.py tests/arena/test_coordinator.py -q
uv run pytest tests/ -q
```

Expected: focused matrix and full suite pass.

- [ ] **Step 5: Commit the offline gate**

```bash
git add src/civ_mcp/arena/channel_queue.py src/civ_mcp/arena/channel_web.py src/civ_mcp/arena/channel_runtime.py src/civ_mcp/arena/coordinator.py tests/arena/test_channel_web.py tests/arena/test_channel_queue.py tests/arena/test_channel_runtime.py tests/arena/test_coordinator.py
git commit -m "test(arena): harden human channel surface"
```

### Task 10: Human/master smoke config, attended LAN gate, and final verification

**Files:**
- Create: `experiments/arena-channels-human-smoke.yaml`
- Create: `docs/superpowers/plans/2026-07-16-arena-channels-human-live-gate.md`

**Interfaces:**
- Consumes: all prior tasks, reviewed two-GPU master setup, live Civ 6.
- Produces: authenticated LAN send/propose/respond/payment/restart/analyst/outage evidence.

- [ ] **Step 1: Create exact smoke experiment**

```yaml
run_id: arena-channels-human-smoke
max_game_turns: 20
channels_analyst: true
master:
  mode: private_adviser
  provider: local
  model: qwen3.6-27b
  gateway: http://192.168.20.196:11441/v1
  personality: "Patient, theatrical, and fascinated by shifting alliances."
  goals:
    - "Create consequential diplomatic choices."
  max_briefing_chars: 1200
  memory_chars: 4000
  timeout_s: 60
  adjudication_grace_turns: 2
civs:
  - player: 0
    provider: human
    model: ""
    briefing:
      enabled: true
      sections: [overview, units, cities, map, research, production_options, rivals, threats, victory]
    channels: {enabled: true}
  - player: 1
    provider: local
    model: gemma4-26b
    gateway: http://192.168.20.196:11440/v1
    tools: full
    channels: {enabled: true}
```

- [ ] **Step 2: Write exact LAN checklist and start commands**

Require `offload-discipline`, `civ6-arena-live`, and `verification-before-completion`; network checks may use normal OS tools without local inference. Record branch/commit/run/save, bind address, client address, token-present boolean (never token), HTTP statuses, queue/view revisions, canonical event IDs, payment fingerprint, restart cursor, contamination event, game turns, and child/server PIDs.

Start arena without managed web, then explicit LAN server so non-loopback auth is exercised:

```bash
uv run civ-arena --config experiments/arena-channels-human-smoke.yaml
CIV_MCP_CHANNELS_TOKEN='<set interactively; do not log>' uv run civ-arena-channels --run-dir arena_runs/arena-channels-human-smoke --host 0.0.0.0 --port 8765 --analyst
```

- [ ] **Step 3: Exercise authenticated round trip and restart**

From a LAN browser: authenticate; receive seat-0 handoff/master briefing once; send prose; propose one deterministic and one narrative deal; accept/decline; fund and accept/reject exact official payment; verify streamed receipts/status/grievance. Stop coordinator after a queued/applied boundary, restart from the same run, verify cursor/request id prevents duplication and deferred payment resumes only when seat 0 is local.

- [ ] **Step 4: Exercise analyst contamination and outage isolation**

Authenticate to analyst stream; verify no full data arrives before `analyst_accessed` is fsynced and coordinator publishes `privacy_contaminated=true`; then verify full read-only view. Stop web process while arena/human turns continue, restart web, and verify it resumes from current projection without session state. Confirm no token in logs/URLs/files.

- [ ] **Step 5: Run final verification and commit**

Run:

```bash
uv run pytest tests/ -q
git diff --check
git status --short --branch
```

Expected: full suite passes; diff check is silent; every LAN checklist item has recorded PASS evidence; only intentional human-surface files and pre-existing `.serena/memories/` appear.

```bash
git add experiments/arena-channels-human-smoke.yaml docs/superpowers/plans/2026-07-16-arena-channels-human-live-gate.md
git commit -m "test(arena): gate human channel surface on LAN"
```

Leave `arena-channels-human-surface` unmerged for riz's separate-session review.
