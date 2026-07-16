# Arena Channels Human Surface — Seat-0 Control & Analyst View (Design)

**Date:** 2026-07-16
**Status:** Approved by riz (written-spec review, 2026-07-16)
**Depends on:** deterministic channels core; integrates with active master metadata when present

## Goal

Let a human-controlled seat 0 participate in unofficial channels from a LAN web page without giving the web server authority over arena state. Provide a separate, explicitly contaminating analyst view for spectating full channel/master behavior.

## Authority Boundary

The coordinator-owned `ChannelRuntime` remains the only canonical-state writer and the only process allowed to invoke channel transitions or linked payment game actions. The web process:

- reads atomic generated projections;
- validates HTTP shape/authentication;
- appends typed requests to a queue;
- streams projection changes.

It never reads or writes `channels/state.json` or `channels/events.jsonl`, never connects to FireTuner, never invokes a model, and cannot block arena turns.

## Architecture and File Boundaries

| File | Responsibility |
|---|---|
| `src/civ_mcp/arena/channel_queue.py` | Shared queue envelopes, append/fsync, incremental reader, cursor/idempotency records |
| `src/civ_mcp/arena/channel_web.py` | FastAPI app, authentication, projection streaming, inline seat-0/analyst pages, standalone CLI |
| `src/civ_mcp/arena/channel_runtime.py` | Drain validated queue records, emit receipts, and atomically publish projections |
| `src/civ_mcp/arena/channels.py` | Seat-0 and analyst projection serialization/contamination metadata |
| `src/civ_mcp/arena/config.py` | Add the explicit `human` seat driver and managed-web/analyst runtime flags |
| `src/civ_mcp/arena/experiment.py` | Validate that a managed surface targets channel-enabled human seat 0 |
| `src/civ_mcp/arena/coordinator.py` | Poll the queue while the human seat is local without auto-playing or auto-ending its turn |
| `src/civ_mcp/arena/arena.py` | `--channels-web` validation and managed-child lifecycle |
| `pyproject.toml` | `civ-arena-channels` console entry point |

The web and CLI enqueue paths share `channel_queue.py`; neither imports FireTuner/game-state code. Queue reduction and projection generation remain runtime responsibilities.

## Run-Directory Contract

The runtime atomically generates:

- `channels/views/seat0.json`: structured `project_for_player(0)` plus UI metadata;
- `channels/views/seat0_view.md`: equivalent readable fallback;
- `channels/views/analyst.json`: full projection, only when analyst mode is explicitly enabled;
- `channels/seat0_queue.jsonl`: append-only human requests.

Every JSON projection contains:

```json
{
  "schema_version": 1,
  "run_id": "arena-20260716-001",
  "revision": 91,
  "generated_at": "2026-07-16T12:00:00Z",
  "player_id": 0,
  "privacy_contaminated": false,
  "channels": {},
  "recent_receipts": []
}
```

Seat-0 UI metadata includes the registered term schemas, allowed counterparties, normalized bounds, legal actions for the current projection, current master mode, and the latest seat-0 master briefing when available. The page derives forms from that metadata rather than maintaining a second term catalog.

The web server has no fallback to the canonical ledger if a projection is missing or corrupt. It returns a clear unavailable state until the runtime publishes a valid projection.

## Queue Protocol

`POST /actions` accepts the same typed action union as `channel_protocol.py`: send message, propose deal, respond to unofficial deal, fund deal, and respond to payment. The server creates or validates a request UUID and appends exactly one compact JSON object plus newline.

Example:

```json
{
  "schema_version": 1,
  "record_type": "player_action",
  "request_id": "9c9544a8-2acd-4ea3-9af1-84a4b55f8e11",
  "actor": 0,
  "submitted_at": "2026-07-16T12:01:02Z",
  "action": {
    "action": "respond_to_deal",
    "deal_id": "deal-000031",
    "accept": true
  }
}
```

The queue append uses one `O_APPEND` write and `fsync`; maximum encoded record size is 8 KiB. A `player_action` record is always actor 0, and the server is not allowed to choose another player actor.

The only other version-1 record type is a server-authored audit record:

```json
{
  "schema_version": 1,
  "record_type": "audit",
  "request_id": "2de0a816-c803-445f-9865-47fb1379284e",
  "submitted_at": "2026-07-16T12:03:00Z",
  "event": "analyst_accessed"
}
```

Audit records have no player actor and cannot be submitted through `POST /actions`. The runtime recognizes only the enumerated `analyst_accessed` event; unknown record types/events are rejected receipts rather than extensible commands.

The runtime stores the byte cursor and applied request IDs in canonical channel state. It drains complete lines in order on coordinator polls:

- pure ledger actions can apply on any poll;
- `fund_deal` and `respond_to_payment` wait until seat 0 is local and the InGame action is safe;
- malformed/unauthorized/stale requests advance the cursor and create a rejected receipt;
- a crash after state application but before cursor advancement may replay the line, but request-ID idempotency makes it a no-op;
- receipts (`applied`, `deferred`, `rejected`, `duplicate`) appear in the next seat-0 projection.

The queue remains append-only for the run. Rotation is not required within the v1 8-KiB-per-action and arena-run bounds.

## Human Participation

Human seat 0 receives the same privacy projection and channel rules as an LLM seat. The runtime continues to tick deadlines, verification, payment, and grievances even when seat 0 is human-pending.

The page supports:

- reading new messages, unresolved deals, payment status, grievances, master briefings made available to seat 0, and receipts;
- composing prose;
- proposing deterministic terms from registry-derived forms;
- proposing narrative terms only when an active master permits them;
- accepting/declining unofficial deals;
- funding and accepting/rejecting linked official payments.

The page never exposes a control that the current projection/rules say is illegal.

## FastAPI Surface

A dedicated module and console entry point serve:

- `GET /`: self-contained seat-0 page shell;
- `GET /events`: authenticated `text/event-stream` seat-0 snapshots;
- `POST /actions`: validate and append one queue action;
- `GET /analyst`: full read-only analyst shell when enabled;
- `GET /analyst/events`: authenticated full snapshots when enabled;
- `GET /healthz`: process and projection health without private contents.

The browser uses streaming `fetch()` rather than `EventSource` so it can send an authorization header. When a token is required, the data-free page shell asks for it and keeps it only in JavaScript memory; reload requires re-entry. The shell itself contains no private projection. The server polls atomic projection mtime/revision about once per second, emits only newer revisions, and sends bounded heartbeats. Channel traffic is turn-paced; no websocket or database is needed.

The page uses inline HTML/CSS/JS, `textContent` for untrusted strings, no external assets, and a restrictive Content Security Policy. It is intentionally separate from the Convex-backed public `web/` application.

## Authentication and Network Safety

- Default bind: `127.0.0.1`.
- Non-loopback bind fails startup unless `CIV_MCP_CHANNELS_TOKEN` is non-blank.
- Private endpoints accept `Authorization: Bearer <token>` when configured.
- Tokens never appear in URLs, projections, logs, or HTML.
- CORS is disabled; mutating requests validate origin/host and require JSON.
- Analyst endpoints use the same authentication and an explicit startup flag.
- There are no third-party scripts, remote fonts, or rendered model HTML.

Caddy/Cloudflare mapping is out of scope for this repo and must not weaken the application token rule for non-loopback binding.

## Analyst Mode and Contamination

`analyst.json` and analyst routes do not exist unless explicitly enabled. Before returning the first authenticated analyst snapshot, the server durably appends and fsyncs an `analyst_accessed` audit record; if that append fails, access fails. The stream withholds analyst data until a later projection revision confirms `privacy_contaminated=true`; if the coordinator is unavailable, no private analyst snapshot is disclosed. The analyst page shell is data-free, so loading the shell alone is not an access event.

The analyst projection may include all messages, deals, deterministic evidence, game-master rulings, grievances, model/cost metadata, and per-player activity. It is read-only. Analyst access grants no additional queue actor or game action.

## Process Lifecycle and CLI Fallback

`civ-arena-channels --run-dir PATH --host 127.0.0.1 --port 8765` runs standalone.

`civ-arena --channels-web PORT` may spawn the exact same command as a managed child after the run directory is known. `--channels-analyst` explicitly enables analyst projection publication and passes `--analyst` to that child. Arena shutdown terminates only that child; standalone servers are never killed by arena cleanup. Child stdout/stderr go to run-local logs.

The managed form is valid only when player 0 is configured with `provider: human` and `channels.enabled: true`; a human provider has a blank model, no policy/backend, never appears in `puppet_ids`, and remains local until the person ends the Civ 6 turn. Invalid combinations fail experiment validation before game startup. The standalone command may start before a projection exists and reports the unavailable state until one is published.

`civ-arena-channels enqueue --run-dir PATH --json ACTION` validates and appends through the same queue code, providing a web-independent fallback. It never edits canonical state.

## Failure and Recovery

- Web/projection failure has no effect on coordinator polling or civ turns.
- Partial projection reads are prevented by atomic replace.
- Invalid SSE clients cannot mutate state.
- Queue parsing stops only at an incomplete trailing line; it resumes when the append completes.
- Deferred game-side actions remain visible with their reason and retry automatically only at safe seat-0-local polls.
- Restarted web processes resume from projection revision without session state.
- Restarted coordinators resume the persisted queue cursor/applied IDs and cannot double-apply actions.

## Testing and Live Gate

Offline tests use FastAPI's test client and temporary run directories to cover:

- seat-0 projection contains only seat-0 typed records;
- analyst projection is absent unless enabled and may exceed seat-0 scope only when enabled;
- loopback/no-token and non-loopback/token startup rules;
- authorization on streams and mutations;
- action schema, 8-KiB bound, one-line append, request IDs, malformed input, and HTML escaping;
- SSE revision/heartbeat shape and reconnect behavior;
- cursor/replay/deferred/receipt behavior with coordinator/runtime fakes;
- analyst access audit and permanent contamination;
- managed-child versus standalone lifecycle;
- CLI enqueue parity;
- web outage leaves a fake arena loop progressing.

The attended LAN gate verifies an authenticated human send/propose/respond/fund/payment round trip, streaming acknowledgements, queue replay after coordinator restart, analyst contamination, and continued arena turns while the web process is stopped.

## Non-Goals

- No canonical state access from the web process.
- No FireTuner connection or game/model invocation from HTTP handlers.
- No public cloud persistence or Convex integration.
- No unauthenticated LAN bind.
- No mobile-native client or multi-human actor support.
- No attempt to preserve clean experimental privacy after analyst access.
