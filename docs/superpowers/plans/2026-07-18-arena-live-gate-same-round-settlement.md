# Arena Live-Gate Same-Round Settlement (Spec Revision 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the `unofficial_channels_core_v1` gate so the official payment settles in the same round it is offered, and the restart checkpoint/resume verification anchor on durable settlement evidence instead of a live pending deal.

**Architecture:** Delta over the executed revision-1 driver in `src/civ_mcp/arena/live_gate_channels.py`. The phase constants keep their names; the order changes (`fund_upfront` → `accept_upfront_payment` → `restart_required`). Treasury baselines are journaled write-ahead in the seat-turn/game-query paths so the crash-reconcile re-run of `_advance_after_capture` stays read-only against the game. The restart checkpoint verifies a recorded settlement digest; resume additionally requires an `absent` seat-independent payment-state.

**Tech Stack:** Python 3.12, pytest + pytest-asyncio (`uv run pytest tests -q`), existing gate fakes in `tests/arena/live_gate_fakes.py`.

**Spec:** `docs/superpowers/specs/2026-07-17-arena-live-gate-driver-design.md` at revision 2 (Locked Decision 13, revised Phase Machine, revised Restart Handshake, Observed Engine Constraints). Read those sections before starting.

**Prior plan:** `docs/superpowers/plans/2026-07-17-arena-live-gate-driver.md` is executed and partially superseded — do not follow its round schedule or Task 8.

## Global Constraints

- `_advance_after_capture` must remain **read-only against the game** — crash reconcile re-runs it (`_reconcile_started_capture`) and re-journals exactly the events the uncrashed capture would have written. Any new game query must happen in the seat-turn or after-capture *pre-journal* paths, be journaled via `data_recorded`, and be guarded so a recovery re-run does not re-read.
- The engine auto-resolves deals offered to an AI player asynchronously (Observed Engine Constraints). No design may assume a pending official deal survives past the seat window that observes it.
- Fail closed everywhere: new mismatches write `gate_failed` + `result.json` through `self._fail(reason_code, detail=...)` with codes from `_PUBLIC_FAILURE_CODES`.
- No retry may create a second official payment side effect; the channel runtime's payment intent/result reconciliation stays the only retry authority.
- Journal shape discipline: `phase_advanced` events and the successful-run journal shape must be byte-stable for a given path (reconcile depends on it).
- All work on `main` in `/home/riz/projects/civ6-mcp`; run the full suite with `uv run pytest tests -q` (≈2 min) before each commit that touches shared code.

## Authoritative eight-round schedule (drives all test expectations)

Rules: `acceptance_turns: 3, funding_turns: 2, payment_response_turns: 2`, both deals `within=1`, seats admitted 1, 2, 3 per game turn. Test rounds use turns 10–17.

| Round (turn) | api_actor (1) | cli_actor (2) | observer (3) | Phase movement |
|---|---|---|---|---|
| R1 (10) | preflight, canary `send_message`, `propose_deal` up_front | `respond_to_deal` accept | no-action + privacy | preflight → canary_and_upfront_proposal → accept_upfront → fund_upfront |
| R2 (11) | baseline read + `fund_deal` | offer re-verified exact, `respond_to_payment` accept, settlement read | no-action + privacy | fund_upfront → accept_upfront_payment → restart_required at round boundary; watcher exits 75 |
| R3 (12, resumed) | no-action | no-action (up-front favor due turn: routes observed, favor satisfied → honored) | no-action + privacy | restart_verify at attach → await_upfront_favor_deadline → verify_upfront_honored → propose_on_delivery |
| R4 (13) | no-action | `propose_deal` on_delivery | no-action + privacy | → accept_on_delivery |
| R5 (14) | `respond_to_deal` accept | no-action | no-action + privacy | → await_on_delivery_favor |
| R6 (15) | no-action (favor due: treasury observed, satisfied; payment due) | no-action | no-action + privacy | → withhold_on_delivery_funding |
| R7 (16) | no-action | no-action (before `fund_by_turn` 17: nonterminal) | no-action + privacy | (no movement) |
| R8 (17) | no-action | no-action (inclusive funding deadline missed) | no-action + privacy | → verify_funding_breach → verify_terminal_gate → terminal PASS |

8 rounds × 3 seats = **24 captures**; `fund_by_turn = 15 + funding_turns(2) = 17`.

---

### Task 1: Scenario revision constant and capture math

**Files:**
- Modify: `src/civ_mcp/arena/live_gate_channels.py:35` (`SCENARIO_REVISION`), `:133-152` (`minimum_captures`)
- Test: `tests/arena/test_live_gate_channels.py:91-104`

**Interfaces:**
- Produces: `SCENARIO_REVISION == 2`; `minimum_captures(gate_config()) == 24`. Later tasks and the config fingerprint rely on both.

- [ ] **Step 1: Update the two capture-math tests and add the revision pin**

Replace `test_minimum_captures_is_27_for_smoke_rules` and the expectation inside `test_minimum_captures_tracks_funding_turns`:

```python
def test_minimum_captures_is_24_for_smoke_rules():
    # Revision 2: same-round settlement removes the dedicated post-resume
    # payment-response round (spec "Expected eight-round path").
    assert lgc.minimum_captures(gate_config()) == 24


def test_minimum_captures_tracks_funding_turns():
    cfg = gate_config(
        channel_rules=ChannelRules(
            acceptance_turns=3,
            funding_turns=4,
            payment_response_turns=2,
        )
    )
    assert lgc.minimum_captures(cfg) == 30  # two extra withheld rounds x 3 seats


def test_scenario_revision_is_2_for_same_round_settlement():
    assert lgc.SCENARIO_REVISION == 2
```

- [ ] **Step 2: Run to verify all three fail**

Run: `uv run pytest tests/arena/test_live_gate_channels.py -q -k "minimum_captures or scenario_revision_is_2"`
Expected: FAIL — 27 != 24, 33 != 30, 1 != 2.

- [ ] **Step 3: Implement**

In `live_gate_channels.py`: set `SCENARIO_REVISION = 2` and change `minimum_captures`:

```python
def minimum_captures(config) -> int:
    """Seat captures for the expected deterministic path.

    Revision 2 handshake rounds: R1 canary+propose+accept, R2 fund +
    same-round payment settlement (restart boundary) — 2 rounds. Then
    UPFRONT_WITHIN rounds to the up-front favor's inclusive deadline (the
    first post-resume round), 2 rounds for the on-delivery proposal +
    acceptance, ON_DELIVERY_WITHIN rounds to its favor deadline, then
    funding_turns withheld rounds through the inclusive funding deadline.
    8 rounds x 3 seats = 24 with the checked-in rules.
    """

    rounds = (
        2
        + UPFRONT_WITHIN
        + 2
        + ON_DELIVERY_WITHIN
        + config.channel_rules.funding_turns
    )
    return rounds * len(ROLE_CONTRACTS)
```

- [ ] **Step 4: Run the three tests — PASS.** Other lifecycle tests will still be red-free because nothing else changed yet.

Run: `uv run pytest tests/arena/test_live_gate_channels.py -q -k "minimum_captures or scenario_revision"`

- [ ] **Step 5: Commit** — `feat(arena): live-gate revision 2 constants — 24-capture eight-round path`

---

### Task 2: Same-round phase order

**Files:**
- Modify: `src/civ_mcp/arena/live_gate_channels.py` — successor map (`_reconcile_started_capture`, ~`:1217`), `_advance_after_capture` FUND_UPFRONT and ACCEPT_UPFRONT_PAYMENT branches (~`:1423-1480`), attach re-arm condition (~`:281-286`), `_verify_restart` tail (`restart_verified` `next_phase`, ~`:909-914`)
- Test: `tests/arena/test_live_gate_channels.py`

**Interfaces:**
- Consumes: Task 1's revision constant (journal fingerprints change).
- Produces: phase transitions `fund_upfront → accept_upfront_payment` (API capture), `accept_upfront_payment → restart_required` (CLI capture, arms restart), `restart_verified.next_phase == PHASE_AWAIT_UPFRONT_FAVOR_DEADLINE`. Tasks 3–6 build on this order.

- [ ] **Step 1: Write the failing round-2 lifecycle test**

```python
@pytest.mark.asyncio
async def test_round2_settles_payment_same_round_then_requests_restart(tmp_path):
    driver, runtime, gs = await attached_driver(tmp_path)
    await run_gate_round(driver, runtime, gs, 10)
    assert driver._journal.state.phase == lgc.PHASE_FUND_UPFRONT

    await run_gate_round(driver, runtime, gs, 11)

    state = driver._journal.state
    assert state.status == "restart_required"
    # The official payment is consumed inside R2 — settled, not pending.
    assert gs.pending == {}
    assert gs.treasury[1] == 499
    assert gs.treasury[2] == 501
    phases = [
        event["payload"]["phase"]
        for event in read_events(driver)
        if event["kind"] == "phase_advanced"
    ]
    assert phases == [
        lgc.PHASE_CANARY_AND_UPFRONT_PROPOSAL,
        lgc.PHASE_ACCEPT_UPFRONT,
        lgc.PHASE_FUND_UPFRONT,
        lgc.PHASE_ACCEPT_UPFRONT_PAYMENT,
        lgc.PHASE_RESTART_REQUIRED,
    ]
```

If the file has no `read_events` helper, add one next to `public_gate_text`:

```python
def read_events(driver):
    return [
        json.loads(line)
        for line in driver._journal.events_path.read_text().splitlines()
    ]
```

- [ ] **Step 2: Run to verify it fails** — currently R2 stops at `restart_required` with the offer still pending (`gs.pending != {}`) and no `accept_upfront_payment` advance.

Run: `uv run pytest tests/arena/test_live_gate_channels.py::test_round2_settles_payment_same_round_then_requests_restart -q`

- [ ] **Step 3: Implement the reorder**

a. `_advance_after_capture`, `PHASE_FUND_UPFRONT and role == ROLE_API` branch: keep the OFFERED check, fingerprint, `_update_payment_checkpoint(recorded=...)`, and `payment_checkpoint_digest` recording. Delete `self._restart_armed = True`. Change the advance target:

```python
            self._journal.append(
                "phase_advanced",
                {"phase": PHASE_ACCEPT_UPFRONT_PAYMENT, "turn": turn},
            )
```

b. `PHASE_ACCEPT_UPFRONT_PAYMENT and role == ROLE_CLI` branch: keep the SETTLED check, baseline-complete check, and `upfront_favor_due_turn` recording. After them, arm the restart and advance to `PHASE_RESTART_REQUIRED` instead of `PHASE_AWAIT_UPFRONT_FAVOR_DEADLINE`:

```python
            self._restart_armed = True
            self._journal.append(
                "phase_advanced",
                {"phase": PHASE_RESTART_REQUIRED, "turn": turn},
            )
```

c. Successor map in `_reconcile_started_capture`:

```python
            PHASE_FUND_UPFRONT: (PHASE_ACCEPT_UPFRONT_PAYMENT,),
            PHASE_ACCEPT_UPFRONT_PAYMENT: (PHASE_RESTART_REQUIRED,),
```

(replacing the old `PHASE_FUND_UPFRONT: (PHASE_RESTART_REQUIRED,)` and `PHASE_ACCEPT_UPFRONT_PAYMENT: (PHASE_AWAIT_UPFRONT_FAVOR_DEADLINE,)` entries; all other entries unchanged).

d. `_verify_restart` tail: `"next_phase": PHASE_AWAIT_UPFRONT_FAVOR_DEADLINE`.

e. Attach re-arm (`attach`, `elif state.phase == PHASE_RESTART_REQUIRED:`): unchanged in this task (Task 5 retargets it to the settlement digest).

- [ ] **Step 4: Run the new test — PASS.** Expect a wave of failures in old rev-1 lifecycle tests; that is Task 7's sweep, not a reason to revert. Verify specifically:

Run: `uv run pytest tests/arena/test_live_gate_channels.py::test_round2_settles_payment_same_round_then_requests_restart -q`

- [ ] **Step 5: Commit** — `feat(arena): same-round settlement phase order (rev 2)`

---

### Task 3: Durable settlement evidence — baselines, deltas, digest

**Files:**
- Modify: `src/civ_mcp/arena/live_gate_channels.py` — `_seat_turn_inner` (~`:958`, before the `_emit_api` call), `_after_seat_capture_inner` (~`:1196`, after `_check_no_unexpected_acknowledgements` and before the `seat_capture_started` append), the ACCEPT_UPFRONT_PAYMENT advance branch
- Test: `tests/arena/test_live_gate_channels.py`

**Interfaces:**
- Consumes: Task 2's phase order.
- Produces: journal `data_recorded` payloads `settlement_baseline` (`{"turn", "payer_gold", "payee_gold"}`) and `settlement_result` (same keys), plus `settlement_digest` (16-hex string from `_digest_mapping`). Tasks 5–6 verify against `settlement_digest`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_settlement_records_baselines_deltas_and_digest(tmp_path):
    driver, runtime, gs = await drive_to_restart(tmp_path)
    data = driver._journal.state.data
    assert data["settlement_baseline"] == {
        "turn": 11, "payer_gold": 500, "payee_gold": 500,
    }
    assert data["settlement_result"] == {
        "turn": 11, "payer_gold": 499, "payee_gold": 501,
    }
    expected = lgc.ChannelsCoreDriver._digest_mapping({
        "fingerprint": exact_payment_fingerprint(),
        "baseline": data["settlement_baseline"],
        "result": data["settlement_result"],
    })
    assert data["settlement_digest"] == expected


@pytest.mark.asyncio
async def test_settlement_delta_mismatch_fails_closed(tmp_path):
    gs = GateGameState()
    original = gs.respond_to_channel_payment

    async def respond_without_transfer(payer, gold, accept):
        result = await original(payer, gold, accept)
        gs.treasury[payer] += gold      # undo: simulate a phantom settlement
        gs.treasury[payee_id] -= gold
        return result

    payee_id = 2
    gs.respond_to_channel_payment = respond_without_transfer
    driver, runtime, gs = await attached_driver(tmp_path, gs=gs)
    await run_gate_round(driver, runtime, gs, 10)
    await run_gate_round(driver, runtime, gs, 11)
    state = driver._journal.state
    assert state.status == "failed"
    assert "settlement_delta_mismatch" in private_failure_text(driver)
```

`ChannelsCoreDriver` (`live_gate_channels.py:215`) is the driver class holding the `_digest_mapping` staticmethod.

- [ ] **Step 2: Run to verify both fail** (no `settlement_baseline` key yet).

Run: `uv run pytest tests/arena/test_live_gate_channels.py -q -k settlement_`

- [ ] **Step 3: Implement**

a. Baseline write-ahead, in `_seat_turn_inner`, immediately before `self._emit_api(...)` when `role == ROLE_API and phase == PHASE_FUND_UPFRONT`, guarded for recovery replays:

```python
        if (
            role == ROLE_API
            and phase == PHASE_FUND_UPFRONT
            and "settlement_baseline" not in self._journal.state.data
        ):
            baseline = await self._read_settlement_treasuries(gs, turn)
            if baseline is None:
                return base_result
            self._journal.append(
                "data_recorded", {"data": {"settlement_baseline": baseline}}
            )
```

b. Post-settlement read, in `_after_seat_capture_inner`, after `_check_no_unexpected_acknowledgements` succeeds and before the `seat_capture_started` append, with the same replay guard:

```python
        if (
            self.pid_role.get(player_id) == ROLE_CLI
            and journal.state.phase == PHASE_ACCEPT_UPFRONT_PAYMENT
            and "settlement_result" not in journal.state.data
        ):
            result = await self._read_settlement_treasuries(self._gs, turn)
            if result is None:
                return
            journal.append(
                "data_recorded", {"data": {"settlement_result": result}}
            )
```

c. The shared reader (new method on the driver class, next to `_run_preflight`):

```python
    async def _read_settlement_treasuries(self, gs, turn) -> dict | None:
        values = {}
        for key, pid in (
            ("payer_gold", self.role_pid[ROLE_API]),
            ("payee_gold", self.role_pid[ROLE_CLI]),
        ):
            request = ObservationRequest(
                families=frozenset({ObservationFamily.TREASURY})
            )
            observed = await gs.get_channel_observation(pid, turn, request)
            if observed.errors or (
                ObservationFamily.TREASURY not in observed.families_present
            ):
                self._fail(
                    "payment_state_failed",
                    detail={
                        "failure": "settlement_treasury_unreadable",
                        "player_id": pid,
                        "errors": list(observed.errors),
                    },
                )
                return None
            values[key] = observed.treasury_gold
        return {"turn": turn, **values}
```

d. Delta verification + digest, in the ACCEPT_UPFRONT_PAYMENT advance branch, after the SETTLED and baseline-complete checks and before arming the restart (reads only journaled data — reconcile-safe):

```python
            data = state.data
            baseline = data.get("settlement_baseline")
            result = data.get("settlement_result")
            recorded = self._payment_fingerprint
            gold = PAYMENT_GOLD
            if (
                not isinstance(baseline, dict)
                or not isinstance(result, dict)
                or recorded is None
                or baseline.get("turn") != turn
                or result.get("turn") != turn
                or result.get("payer_gold")
                != baseline.get("payer_gold") - gold
                or result.get("payee_gold")
                != baseline.get("payee_gold") + gold
            ):
                self._fail(
                    "payment_checkpoint_failed",
                    detail={
                        "failure": "settlement_delta_mismatch",
                        "baseline": baseline,
                        "result": result,
                    },
                )
                return
            digest = self._digest_mapping(
                {
                    "fingerprint": recorded,
                    "baseline": baseline,
                    "result": result,
                }
            )
            self._journal.append(
                "data_recorded", {"data": {"settlement_digest": digest}}
            )
            self._update_payment_checkpoint(settlement_digest=digest)
```

- [ ] **Step 4: Run the two tests — PASS.**

Run: `uv run pytest tests/arena/test_live_gate_channels.py -q -k settlement_`

- [ ] **Step 5: Commit** — `feat(arena): durable same-turn settlement evidence (rev 2)`

---

### Task 4: `official_payment_auto_resolved` pre-acceptance check

**Files:**
- Modify: `src/civ_mcp/arena/live_gate_channels.py` — `_PUBLIC_FAILURE_CODES` (~`:87`), `_seat_turn_inner` CLI path (before `_emit_cli`)
- Test: `tests/arena/test_live_gate_channels.py`

**Interfaces:**
- Consumes: Task 2's phase order (CLI acts in R2).
- Produces: terminal failure reason `official_payment_auto_resolved` whenever the offer is not `exact` at acceptance time and the channel deal is not already settled.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_auto_resolved_offer_at_acceptance_is_distinct_terminal(tmp_path):
    driver, runtime, gs = await attached_driver(tmp_path)
    await run_gate_round(driver, runtime, gs, 10)
    # R2: API funds, then the engine AI eats the offer before the CLI window.
    await run_gate_seat(driver, runtime, gs, 1, 11)
    gs.pending.clear()          # simulate engine auto-resolution
    await run_gate_seat(driver, runtime, gs, 2, 11)
    state = driver._journal.state
    assert state.status == "failed"
    assert state.reason == "official_payment_auto_resolved"
```

- [ ] **Step 2: Run to verify it fails** — today the CLI's `respond_to_payment` produces a runtime error ack (`NO_EXACT_CHANNEL_PAYMENT`) and a generic failure, not the distinct reason.

Run: `uv run pytest tests/arena/test_live_gate_channels.py::test_auto_resolved_offer_at_acceptance_is_distinct_terminal -q`

- [ ] **Step 3: Implement**

a. Add `"official_payment_auto_resolved"` to `_PUBLIC_FAILURE_CODES`.

b. In `_seat_turn_inner`, before the `_emit_cli(...)` return, when the CLI is about to respond to the payment (skip when the channel deal already reports SETTLED — that is a crash-recovery replay, owned by payment reconciliation):

```python
        if role == ROLE_CLI and phase == PHASE_ACCEPT_UPFRONT_PAYMENT:
            deal = self._deal(self._journal.state.data["upfront_deal_id"])
            if deal is None:
                return base_result
            from civ_mcp.arena.channels import PaymentStatus

            if deal.payment_status is not PaymentStatus.SETTLED:
                payment_state = await gs.get_channel_payment_state(
                    self.role_pid[ROLE_API],
                    self.role_pid[ROLE_CLI],
                    PAYMENT_GOLD,
                )
                status = getattr(payment_state, "status", None)
                status = getattr(status, "value", status)
                if status != "exact":
                    self._fail(
                        "official_payment_auto_resolved",
                        detail={
                            "payer": self.role_pid[ROLE_API],
                            "payee": self.role_pid[ROLE_CLI],
                            "status": status,
                        },
                    )
                    return base_result
```

- [ ] **Step 4: Run the test — PASS.** Also re-run Task 2/3 tests to confirm the happy path still settles.

Run: `uv run pytest tests/arena/test_live_gate_channels.py -q -k "auto_resolved or settlement_ or same_round"`

- [ ] **Step 5: Commit** — `feat(arena): distinct official_payment_auto_resolved forensic (rev 2)`

---

### Task 5: Restart checkpoint on durable evidence

**Files:**
- Modify: `src/civ_mcp/arena/live_gate_channels.py` — `_request_restart` (~`:1865`), delete `_live_offer_fingerprint` (~`:1907-1938`), attach re-arm condition (~`:282`)
- Test: `tests/arena/test_live_gate_channels.py`

**Interfaces:**
- Consumes: Task 3's `settlement_digest`.
- Produces: `_request_restart` that performs no game query; re-arm on attach keyed to `state.data["settlement_digest"]`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_restart_checkpoint_uses_no_live_payment_query(tmp_path):
    gs = GateGameState()
    calls = []
    original = gs.get_channel_payment_state

    async def counting_state(payer, payee, gold):
        calls.append((payer, payee, gold))
        return await original(payer, payee, gold)

    gs.get_channel_payment_state = counting_state
    driver, runtime, gs = await drive_to_restart(tmp_path, gs=gs)
    assert driver._journal.state.status == "restart_required"
    # Preflight (R1) and the CLI pre-acceptance check (R2) are the only
    # sanctioned live payment-state queries; the round-boundary checkpoint
    # must add none.
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_restart_without_settlement_digest_fails(tmp_path):
    driver, runtime, gs = await attached_driver(tmp_path)
    await run_gate_round(driver, runtime, gs, 10)
    await run_gate_seat(driver, runtime, gs, 1, 11)
    await run_gate_seat(driver, runtime, gs, 2, 11)
    del driver._journal.state.data["settlement_digest"]  # sabotage
    await run_gate_seat(driver, runtime, gs, 3, 11)
    state = driver._journal.state
    assert state.status == "failed"
    assert state.reason == "restart_checkpoint_failed"
```

If `state.data` is immutable in Step 1's sabotage, monkeypatch `driver._digest_mapping` inconsistency instead — the intent is: checkpoint must fail when the digest is missing or does not recompute.

- [ ] **Step 2: Run to verify both fail** (checkpoint currently queries live state → 3 calls; missing digest is currently ignored).

- [ ] **Step 3: Implement — rewrite `_request_restart`**

```python
    async def _request_restart(self, turn: int) -> None:
        data = self._journal.state.data
        recorded = self._payment_fingerprint
        baseline = data.get("settlement_baseline")
        result = data.get("settlement_result")
        digest = data.get("settlement_digest")
        if not recorded or not digest:
            self._fail(
                "restart_checkpoint_failed",
                detail={"failure": "missing_settlement_evidence"},
            )
            return
        recomputed = self._digest_mapping(
            {"fingerprint": recorded, "baseline": baseline, "result": result}
        )
        if recomputed != digest:
            self._fail(
                "restart_checkpoint_failed",
                detail={
                    "failure": "settlement_digest_mismatch",
                    "recorded": digest,
                    "recomputed": recomputed,
                },
            )
            return
        channel_state = self._runtime.state
        self._journal.append(
            "data_recorded",
            {
                "data": {
                    "restart_channel_sequence": channel_state.last_event_sequence,
                    "restart_turn": turn,
                }
            },
        )
        self._journal.append("restart_required", {"turn": turn})
        self._journal.write_result()
        self._restart_armed = False
        self._signal = GATE_RESTART_REQUIRED
```

Delete `_live_offer_fingerprint` (now unused) and change the attach re-arm line to:

```python
            self._restart_armed = (
                self._journal.state.data.get("settlement_digest") is not None
            )
```

- [ ] **Step 4: Run both tests — PASS.**

Run: `uv run pytest tests/arena/test_live_gate_channels.py -q -k "restart_checkpoint or restart_without"`

- [ ] **Step 5: Commit** — `feat(arena): restart checkpoint anchors on settlement digest (rev 2)`

---

### Task 6: Resume verification (rev 2)

**Files:**
- Modify: `src/civ_mcp/arena/live_gate_channels.py` — `_verify_restart` (~`:800-915`)
- Test: `tests/arena/test_live_gate_channels.py`

**Interfaces:**
- Consumes: Tasks 3 and 5 (digest present at resume).
- Produces: resume that requires SETTLED payment, digest recompute equality, `absent` live payment-state, single settlement ack ≤ `restart_turn`, none after; advances to `await_upfront_favor_deadline`.

- [ ] **Step 1: Write the failing tests** (mirror the file's existing resume idiom — reattach a fresh driver over the same `run_dir`):

```python
@pytest.mark.asyncio
async def test_resume_verifies_settlement_and_continues(tmp_path):
    driver, runtime, gs = await drive_to_restart(tmp_path)
    resumed = await reattach_driver(tmp_path, runtime, gs)
    state = resumed._journal.state
    assert state.status not in ("failed",)
    verified = [e for e in read_events(resumed) if e["kind"] == "restart_verified"]
    assert verified[-1]["payload"]["next_phase"] == lgc.PHASE_AWAIT_UPFRONT_FAVOR_DEADLINE


@pytest.mark.asyncio
async def test_resume_with_stray_official_offer_fails(tmp_path):
    driver, runtime, gs = await drive_to_restart(tmp_path)
    from civ_mcp.lua.channel_payments import ExactPaymentOffer
    gs.pending[(1, 2)] = ExactPaymentOffer(1, 2, 1)   # resurrected offer
    resumed = await reattach_driver(tmp_path, runtime, gs)
    state = resumed._journal.state
    assert state.status == "failed"
    assert state.reason == "restart_verification_failed"
```

Use the existing reattach helper if the file has one (see `test_resume_verifies_offer_and_continues` at ~`:1248` for the established construction); otherwise extract `reattach_driver` from that test verbatim.

- [ ] **Step 2: Run to verify failures** — today resume demands payment OFFERED (now SETTLED) so the happy resume fails, and a stray offer passes.

- [ ] **Step 3: Implement — inside `_verify_restart`**, replacing the OFFERED check and the fingerprint-equality block:

```python
        deal = self._deal(state.data.get("upfront_deal_id"))
        if deal is None:
            return
        if deal.payment_status is not PaymentStatus.SETTLED:
            self._fail(
                "restart_verification_failed",
                detail={
                    "failure": "payment_not_settled_at_resume",
                    "payment_status": str(deal.payment_status),
                },
            )
            return
        recorded = self._payment_fingerprint
        digest = state.data.get("settlement_digest")
        recomputed = self._digest_mapping(
            {
                "fingerprint": recorded,
                "baseline": state.data.get("settlement_baseline"),
                "result": state.data.get("settlement_result"),
            }
        )
        if not digest or recomputed != digest:
            self._fail(
                "restart_verification_failed",
                detail={
                    "failure": "settlement_digest_mismatch",
                    "recorded": digest,
                    "recomputed": recomputed,
                },
            )
            return
        payment_state = await self._gs.get_channel_payment_state(
            self.role_pid[ROLE_API], self.role_pid[ROLE_CLI], PAYMENT_GOLD
        )
        status = getattr(payment_state, "status", None)
        status = getattr(status, "value", status)
        if status != "absent":
            self._fail(
                "restart_verification_failed",
                detail={"failure": "stray_official_offer", "status": status},
            )
            return
```

Then adjust the acknowledgement discipline: keep the existing "no ack **after** `restart_turn`" check, and add "exactly one payment-response ack at or before `restart_turn`" (the settlement ack):

```python
        settled_acks = [
            acknowledgement
            for acknowledgement in self._runtime.state.acknowledgements
            if acknowledgement.deal_id == state.data.get("upfront_deal_id")
            and acknowledgement.player_id == self.role_pid[ROLE_CLI]
            and acknowledgement.turn <= state.data.get("restart_turn", -1)
        ]
```

`ChannelAcknowledgement` (`channels.py:102`) carries `player_id, turn, source_id, status, message, deal_id` — there is no action-name field, so identify the settlement ack by its journaled plan: the `verified_actions` entry whose `name` is `"respond_to_payment"` holds the `source_id`; count acks whose `source_id` equals it. Fail with `{"failure": "settlement_acknowledgement_count", "count": len(settled_acks)}` unless that count is exactly one.

- [ ] **Step 4: Run the resume tests — PASS.**

Run: `uv run pytest tests/arena/test_live_gate_channels.py -q -k resume`

- [ ] **Step 5: Commit** — `feat(arena): rev-2 resume verification — settled evidence + absent offer`

---

### Task 7: Migration sweep to the eight-round schedule, full suite green

**Files:**
- Modify: `tests/arena/test_live_gate_channels.py` (rev-1 lifecycle/deadline/crash tests), possibly `tests/arena/test_arena_wiring.py`
- Test: entire suite

**Interfaces:**
- Consumes: Tasks 1–6.
- Produces: 100% green suite where every lifecycle expectation matches the authoritative eight-round table at the top of this plan.

- [ ] **Step 1: Run the full arena gate test file and list failures**

Run: `uv run pytest tests/arena/test_live_gate_channels.py -q`

- [ ] **Step 2: Fix each failing test against the eight-round table.** Known categories (verify each against the table, do not guess):
  - Tests that drove `respond_to_payment` after resume (e.g. `test_resume_verifies_offer_and_continues`, `test_resume_with_changed_offer_fails`, `test_resume_with_absent_offer_fails`): the payment now settles pre-restart. Changed/absent-offer variants become Task 6's stray-offer/digest-mismatch cases; delete duplicates that Task 6 already covers rather than keeping parallel copies.
  - Deadline tests pinned to turns 12–18: shift one round earlier per the table (up-front favor due turn 12; on-delivery propose 13, accept 14, favor due 15, `fund_by_turn` 17; breach at 17).
  - Crash/reconcile tests exercising the `FUND_UPFRONT → RESTART_REQUIRED` chain: retarget to `FUND_UPFRONT → ACCEPT_UPFRONT_PAYMENT` and `ACCEPT_UPFRONT_PAYMENT → RESTART_REQUIRED` chains.
  - `test_restart_checkpoint_persists_fingerprint_and_result` and payment-checkpoint reconcile tests: the private checkpoint now carries `settlement_digest` in addition to `recorded`.
  - Terminal-PASS lifecycle test: total rounds 8, captures 24.
- [ ] **Step 3: Full suite**

Run: `uv run pytest tests -q`
Expected: all green (baseline was 1930; net count will shift with added/removed tests).

- [ ] **Step 4: Commit** — `test(arena): migrate gate lifecycle suite to rev-2 eight-round schedule`

---

### Task 8: Attended rerun readiness check

**Files:**
- Verify (no code): `experiments/arena-channels-core-smoke.yaml` (`run_id: arena-channels-core-gate-v3`, already landed with the review fixes), spec cross-references

**Steps:**

- [ ] **Step 1:** `uv run pytest tests/arena/test_experiment.py tests/arena/test_config.py -q` — config validation accepts the 36 budgets against the computed 24 minimum.
- [ ] **Step 2:** Confirm no stale references: `grep -rn 'gate-v2\|nine-round\|27 seat' src tests docs/superpowers/specs experiments` → only historical mentions (run directories, v2 postmortems) may remain; no live config/code/test may reference them as current.
- [ ] **Step 3: Commit** any stragglers — `chore(arena): rev-2 rerun readiness`.

**Live procedure after this plan (operator-attended, not part of the plan):** reload `CHANNELS_GATE_V1_T157`, arm via the `civ6-arena-live` workflow with run `arena-channels-core-gate-v3`, two-invocation exit-75 handshake, rearm fast in the gap, expect terminal PASS at R8.

## Self-Review Notes

- Spec coverage: Locked Decision 13 → Tasks 2–6; revised Phase Machine rows → Tasks 2–4; revised Restart Handshake list → Tasks 5–6; 24-capture budget → Task 1; reviewer finding 6's five evidence tests → Tasks 3 (deltas), 4 (auto-resolved), 5 (digest at checkpoint), 6 (digest across resume, absent payment-state, ack discipline).
- The `_digest_mapping` call sites in test code must use the real driver class symbol — flagged inline in Task 3 Step 1.
- Round arithmetic cross-checked against `minimum_captures` (2 + 1 + 2 + 1 + 2 = 8) and the deadline math from the executed plan shifted one round earlier.
