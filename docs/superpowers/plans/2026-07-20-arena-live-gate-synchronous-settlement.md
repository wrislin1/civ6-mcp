# Arena Live-Gate Synchronous Fund-Window Settlement (Spec Revision 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the `unofficial_channels_core_v1` gate and the channel payment runtime so official-payment settlement is verified synchronously in the funding seat window, and the payee's `respond_to_payment` becomes channel-ledger bookkeeping that performs no engine mutation.

**Architecture:** Delta over the executed revision-2 driver in `src/civ_mcp/arena/live_gate_channels.py` plus a production-semantics change in `src/civ_mcp/arena/channel_runtime.py`. Ledger lifecycle keeps `DUE → OFFERED → SETTLED`: `fund_deal` still transitions to `OFFERED` (engine enactment verified by the gate in the same window), and the payee's response transitions to `SETTLED` with **no engine call** — its preflight now expects the seat-independent payment state `absent`. The settlement-evidence read (treasuries + post-send state) moves from the CLI acceptance capture to the API fund capture; `_advance_after_capture` stays read-only against the game (crash reconcile re-runs it).

**Tech Stack:** Python 3.12, pytest + pytest-asyncio (`uv run pytest tests -q`), gate fakes in `tests/arena/live_gate_fakes.py`.

**Spec:** `docs/superpowers/specs/2026-07-17-arena-live-gate-driver-design.md` at revision 3 (Locked Decision 14, revised `fund_upfront`/`accept_upfront_payment` phase rows, revised Restart Handshake, Observed Engine Constraints 2026-07-20). Read those sections before starting.

**Prior plans:** `docs/superpowers/plans/2026-07-18-arena-live-gate-same-round-settlement.md` is executed (commits `a52c73c..77b3a72`) and superseded in the settlement-verification placement: its Task 3 (settlement result read at CLI acceptance capture) and Task 4 (`official_payment_auto_resolved` forensic) are reversed by this plan. Its restart checkpoint (Task 5) and resume verification (Task 6) carry over unchanged.

**Live evidence grounding:** attended run `arena-channels-core-gate-v3` (`arena_runs/arena-channels-core-gate-v3/live_gate/events.jsonl` seq 27–34) and the deal-lifecycle probe (`arena_runs/arena-channels-core-gate-v3/probe_deal_lifecycle_result.txt`): the engine enacts an AI→AI `PROPOSED` deal synchronously at send — `pending=false` and exact −1.000/+1.000 deltas on the first sub-second poll, stable 300 s, mid-turn treasuries otherwise static.

## Global Constraints

- `_advance_after_capture` must remain **read-only against the game** — crash reconcile re-runs it (`_reconcile_started_capture`) and re-journals exactly the events the uncrashed capture would have written. All new game reads happen in `_record_settlement_result` (the pre-journal path before `seat_capture_started`), are journaled via `data_recorded` through `_record_data_once`, and are guarded so a recovery re-run does not re-read.
- The engine enacts AI→AI `PROPOSED` deals synchronously at send (Locked Decision 14). No design may expect an `exact` pending official offer at any time after `offer_channel_payment` returns.
- Fail closed everywhere: new mismatches write `gate_failed` + `result.json` through `self._fail(reason_code, detail=...)` with codes registered in `_PUBLIC_FAILURE_CODES` (`live_gate_channels.py:90`).
- No retry may create a second official payment side effect; the channel runtime's payment intent/result reconciliation stays the only retry authority. A fund-intent crash whose recovery observes `absent` without same-window delta evidence stays `unverifiable` (fail-closed) — this plan does not add delta-based fund recovery.
- Journal shape discipline: `phase_advanced` events and the successful-run journal shape must be byte-stable for a given path.
- The eight-round schedule and 24-capture minimum from the rev-2 plan are **unchanged** — no round is added or removed; only the in-round work moves between seat windows.
- All work on `main` in `/home/riz/projects/civ6-mcp`; run the full suite with `uv run pytest tests -q` (≈2 min) before each commit that touches shared code.

## Revised round-2 content (all other rounds unchanged from the rev-2 plan)

| Round (turn) | api_actor (1) | cli_actor (2) | observer (3) | Phase movement |
|---|---|---|---|---|
| R2 (11) | baseline read + `fund_deal` + **post-send absent check + exact-delta check + settlement digest** | `respond_to_payment` accept (**ledger-only**; pre-response state must be `absent`) | no-action + privacy | fund_upfront → accept_upfront_payment → restart_required at round boundary; watcher exits 75 |

`minimum_captures(gate_config()) == 24` stays true; `SCENARIO_REVISION` becomes `3`.

---

### Task 1: Runtime — payee response becomes ledger bookkeeping

**Files:**
- Modify: `src/civ_mcp/arena/channel_runtime.py` — `_validate_payment_intent` (expected_preflight, ~line 486), `_respond_to_payment` (~line 2150: preflight check, engine call removal), replay result validation (~lines 1248–1305)
- Test: `tests/arena/test_channel_runtime.py`

**Interfaces:**
- Produces: module constant `ENACTED_ABSENT_RESULT = "CHANNEL_PAYMENT_ENACTED_ABSENT"` (the authoritative engine evidence string recorded when the response commits against an absent enacted payment). Tasks 2 and 7 rely on it.
- Produces: `_respond_to_payment` accepts when the seat-independent state is `absent`, commits `payment_response_intent` with `preflight_status="absent"`, calls **no** engine mutation, and commits `payment_response_result` with `engine_result=ENACTED_ABSENT_RESULT`, transitioning the deal `OFFERED → SETTLED` via the existing `success_deal` path.

- [ ] **Step 1: Write the failing tests**

In `tests/arena/test_channel_runtime.py`, next to the existing respond-path tests (search `respond_to_payment`), using that file's existing fixtures for a runtime holding an `OFFERED` up-front deal:

```python
class _AbsentStateGS:
    """Engine truth: the enacted payment is absent; any engine mutation is a bug."""

    def __init__(self, observation_gs):
        self._inner = observation_gs
        self.respond_calls = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def get_channel_payment_state(self, payer, payee, gold):
        from tests.arena.live_gate_fakes import PaymentStateView
        return PaymentStateView("absent")

    async def respond_to_channel_payment(self, payer, gold, accept):
        self.respond_calls += 1
        raise AssertionError("revision 3: response must not mutate the engine")


async def test_payment_response_settles_ledger_against_absent_enacted_payment(
    offered_runtime, offered_gs, offered_deal_id, staged_response_accept
):
    gs = _AbsentStateGS(offered_gs)
    ack = await offered_runtime.apply_staged(gs, staged_response_accept, turn=TURN)
    assert ack.status == "applied"
    assert gs.respond_calls == 0
    deal = offered_runtime.deal(offered_deal_id)
    assert deal.payment_status is PaymentStatus.SETTLED
    result = [
        e for e in offered_runtime.state_events()
        if e.kind == "payment_response_result"
    ][-1]
    assert result.payload["engine_result"] == cr.ENACTED_ABSENT_RESULT
    intent = [
        e for e in offered_runtime.state_events()
        if e.kind == "payment_response_intent"
    ][-1]
    assert intent.payload["preflight_status"] == "absent"
    assert intent.payload["preflight_player"] == deal.counterparty


async def test_payment_response_rejects_unexpectedly_pending_offer(
    offered_runtime, offered_gs, staged_response_accept
):
    # offered_gs still models a pending exact offer -> now the anomaly
    ack = await offered_runtime.apply_staged(
        offered_gs, staged_response_accept, turn=TURN
    )
    assert ack.status == "rejected"
    assert "unexpectedly pending" in ack.message
```

Adapt fixture names to the file's local ones (the file already stages accepted responses against an `OFFERED` deal); the assertions above are the contract. Add a validation-side test that a journaled `payment_response_intent` with `preflight_status="exact"` is now rejected by `_validate_payment_intent` (mirror the existing invalid-preflight test with the statuses swapped).

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/arena/test_channel_runtime.py -q -k "payment_response"`
Expected: the new absent-state test fails with `rejected` / "the exact linked payment offer is not pending"; the unexpectedly-pending test fails because the exact offer currently succeeds.

- [ ] **Step 3: Implement**

In `channel_runtime.py`:

1. Add near the other result-string helpers:

```python
ENACTED_ABSENT_RESULT = "CHANNEL_PAYMENT_ENACTED_ABSENT"
```

2. `_validate_payment_intent` (~line 486): flip the response expectation —

```python
        expected_preflight = (
            ("absent", deal.proposer)
            if kind == "payment_fund_intent"
            else ("absent", deal.counterparty)
        )
```

3. `_respond_to_payment` (~line 2214): replace the exact-offer gate —

```python
        if self._payment_state_status(payment_state, deal) != "absent":
            raise _ActionRejected(
                "the linked payment offer is unexpectedly pending"
            )
```

and pass `preflight_status="absent"` through `_payment_intent_payload` (it derives preflight from the queried state — confirm the recorded payload carries `absent`/counterparty).

4. Delete the entire `gs.respond_to_channel_payment` call block (the `engine_accept` computation, the try/except recovery wrapper, and the `_response_succeeded` branching, ~lines 2234–2299). After `_commit_payment_intent_for_action` returns `None`, commit directly:

```python
        engine_result = ENACTED_ABSENT_RESULT
        if cleanup_deal is not None:
            return self._commit_payment_result(
                "payment_response_result",
                intent,
                engine_result=engine_result,
                recovery=None,
                deal=cleanup_deal,
                grievance=None,
                message=f"payment acceptance became unverifiable for {deal.id}",
            )
        if action.accept:
            return self._commit_payment_result(
                "payment_response_result",
                intent,
                engine_result=engine_result,
                recovery=None,
                deal=success_deal,
                grievance=None,
                message=f"accepted linked payment for {deal.id}",
            )
        broken, grievance = self._broken_deal_records(
            deal,
            turn=turn,
            breach="payment_response",
            reason="enacted linked payment was rejected",
        )
        return self._commit_payment_result(
            "payment_response_result",
            intent,
            engine_result=engine_result,
            recovery=None,
            deal=broken,
            grievance=grievance,
            message=f"rejected linked payment for {deal.id}",
        )
```

5. Replay-side result validation (~lines 1248–1305): where journaled `payment_response_result` events are validated via `_response_succeeded(engine_result, engine_accept)`, accept `ENACTED_ABSENT_RESULT` as the success string for both accept and reject responses (it is the recorded engine evidence in every rev-3 response). Keep validation of legacy strings so rev-1/-2 journals still replay.

- [ ] **Step 4: Run the runtime suite — new tests PASS; note collateral failures**

Run: `uv run pytest tests/arena/test_channel_runtime.py -q`
Collateral failures in old respond-path tests that model the disproved engine (pending offers surviving to the response) are expected — migrate them now: any test staging a response against a fake still holding an `exact` pending offer either becomes an `unexpectedly pending` rejection test or updates its fake to the absent-enacted shape. Do not delete coverage: every migrated test must still assert ledger `SETTLED`/broken transitions and single-ack discipline.

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/channel_runtime.py tests/arena/test_channel_runtime.py
git commit -m "feat(arena): payment response settles ledger without engine mutation (rev 3)"
```

---

### Task 2: Runtime — response-intent reconciliation treats absent as completion

**Files:**
- Modify: `src/civ_mcp/arena/channel_runtime.py` — `reconcile_payment_intents`, response branch (~lines 2672–2700+)
- Test: `tests/arena/test_channel_runtime.py`

**Interfaces:**
- Consumes: `ENACTED_ABSENT_RESULT` from Task 1.
- Produces: a crashed `payment_response_intent` (intent journaled, result missing) whose recovery observes `absent` completes with the intent's recorded `accept`/`success_deal`/`cleanup` and `recovery="observed_absent_enacted"`; an `exact` or `conflicting` observation at recovery is the anomalous branch and commits the existing unverifiable record.

- [ ] **Step 1: Write the failing tests**

Mirror the file's existing reconcile tests (search `reconcile_payment_intents`):

```python
async def test_response_intent_recovery_completes_on_absent(
    runtime_with_unfinished_response_intent, absent_gs, deal_id
):
    rt = runtime_with_unfinished_response_intent
    await rt.reconcile_payment_intents(
        absent_gs, current_turn=TURN, current_player_id=PAYEE
    )
    deal = rt.deal(deal_id)
    assert deal.payment_status is PaymentStatus.SETTLED
    result = [
        e for e in rt.state_events() if e.kind == "payment_response_result"
    ][-1]
    assert result.payload["recovery"] == "observed_absent_enacted"
    assert result.payload["engine_result"] == cr.ENACTED_ABSENT_RESULT


async def test_response_intent_recovery_unverifiable_on_exact(
    runtime_with_unfinished_response_intent, exact_gs, deal_id
):
    rt = runtime_with_unfinished_response_intent
    await rt.reconcile_payment_intents(
        exact_gs, current_turn=TURN, current_player_id=PAYEE
    )
    deal = rt.deal(deal_id)
    assert deal.is_terminal  # unverifiable record
```

- [ ] **Step 2: Run to verify both fail**

Run: `uv run pytest tests/arena/test_channel_runtime.py -q -k "response_intent_recovery"`
Expected: absent-recovery currently commits `RECOVERY_PAYMENT_ABSENT` unverifiable → first test fails; exact-recovery currently *completes* via the engine call → second fails.

- [ ] **Step 3: Implement**

In the reconcile response branch, swap the roles of `absent` and `exact`:

```python
            if status != "absent":
                unverifiable = self._unverifiable_deal_record(
                    deal,
                    turn=current_turn,
                    reason=(
                        "payment response intent outcome is ambiguous because "
                        f"an offer is unexpectedly {status}"
                    ),
                )
                self._commit_payment_result(
                    "payment_response_result",
                    intent,
                    engine_result="RECOVERY_PAYMENT_UNEXPECTEDLY_PENDING",
                    recovery="conflicting_offer",
                    deal=unverifiable,
                    grievance=None,
                    message=f"payment response became unverifiable for {deal.id}",
                )
                continue
```

then complete the absent path with the intent's recorded outcome (no engine call): rebuild `success_deal`/`cleanup_deal`/`broken` exactly as `_respond_to_payment` does from the intent payload, and commit `payment_response_result` with `engine_result=ENACTED_ABSENT_RESULT`, `recovery="observed_absent_enacted"`.

- [ ] **Step 4: Run the tests — PASS**, then the file: `uv run pytest tests/arena/test_channel_runtime.py -q`

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/channel_runtime.py tests/arena/test_channel_runtime.py
git commit -m "fix(arena): reconcile response intents against synchronous enactment"
```

---

### Task 3: Driver constants — revision 3 and the new failure codes

**Files:**
- Modify: `src/civ_mcp/arena/live_gate_channels.py:35` (`SCENARIO_REVISION`), `:90–96` (`_PUBLIC_FAILURE_CODES`)
- Test: `tests/arena/test_live_gate_channels.py`

**Interfaces:**
- Produces: `SCENARIO_REVISION == 3`; `_PUBLIC_FAILURE_CODES` contains `"official_payment_not_enacted"` and `"official_payment_unexpectedly_pending"` and no longer contains `"official_payment_auto_resolved"`. `minimum_captures(gate_config()) == 24` is unchanged.

- [ ] **Step 1: Update/replace the revision test and add the code-registry test**

```python
def test_scenario_revision_is_3_for_synchronous_settlement():
    assert lgc.SCENARIO_REVISION == 3


def test_revision_3_failure_codes_registered():
    assert "official_payment_not_enacted" in lgc._PUBLIC_FAILURE_CODES
    assert "official_payment_unexpectedly_pending" in lgc._PUBLIC_FAILURE_CODES
    assert "official_payment_auto_resolved" not in lgc._PUBLIC_FAILURE_CODES
```

Replace `test_scenario_revision_is_2_for_same_round_settlement`.

- [ ] **Step 2: Run to verify both fail** — `uv run pytest tests/arena/test_live_gate_channels.py -q -k "revision"`

- [ ] **Step 3: Implement** — set `SCENARIO_REVISION = 3`; in `_PUBLIC_FAILURE_CODES` replace `"official_payment_auto_resolved"` with the two new codes.

- [ ] **Step 4: Run — PASS.** Wider lifecycle failures surface in Tasks 4–7.

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/live_gate_channels.py tests/arena/test_live_gate_channels.py
git commit -m "feat(arena): live-gate revision 3 constants and failure codes"
```

---

### Task 4: Fund-window settlement evidence read

**Files:**
- Modify: `src/civ_mcp/arena/live_gate_channels.py` — `_record_settlement_result` (:1356–1373), `_reconcile_verified_capture` `needs_settlement_read` (:1414–1420)
- Test: `tests/arena/test_live_gate_channels.py`

**Interfaces:**
- Consumes: `_read_settlement_treasuries(gs, turn)` (:2333, unchanged) and `gs.get_channel_payment_state`.
- Produces: after the **API** seat's capture in `PHASE_FUND_UPFRONT`, journal `data` contains `settlement_result` (`{"turn", "payer_gold", "payee_gold"}`) and `post_send_payment_status` (string). Task 5's read-only verification consumes both. The CLI/acceptance capture no longer reads anything.

- [ ] **Step 1: Write the failing test**

```python
async def test_fund_capture_records_settlement_result_and_post_send_status(
    gate_driver_at_fund_upfront, runtime, gs
):
    # fakes updated in Task 7 apply -1/+1 at offer; until then this test
    # drives the fake treasuries directly after the fund action
    await run_gate_seat(gate_driver_at_fund_upfront, runtime, gs, API_PID, TURN)
    data = gate_driver_at_fund_upfront._journal.state.data
    assert data["settlement_result"]["turn"] == TURN
    assert data["post_send_payment_status"] == "absent"
```

Use the file's existing fund-phase lifecycle fixtures (search `PHASE_FUND_UPFRONT` in the test file for the rev-2 arrangement helpers).

- [ ] **Step 2: Run to verify it fails** — today `settlement_result` is only recorded at the CLI acceptance capture.

- [ ] **Step 3: Implement**

Rewrite `_record_settlement_result` to trigger on the API fund capture and also record the post-send state:

```python
    async def _record_settlement_result(self, player_id: int, turn: int) -> bool:
        journal = self._journal
        assert journal is not None
        if (
            self.pid_role.get(player_id) != ROLE_API
            or journal.state.phase != PHASE_FUND_UPFRONT
        ):
            return True
        if (
            "settlement_result" in journal.state.data
            and "post_send_payment_status" in journal.state.data
        ):
            return True
        result = await self._read_settlement_treasuries(self._gs, turn)
        if result is None:
            return False
        try:
            payment_state = await self._gs.get_channel_payment_state(
                self.role_pid[ROLE_API],
                self.role_pid[ROLE_CLI],
                PAYMENT_GOLD,
            )
        except Exception as exc:
            self._fail(
                "payment_state_failed",
                detail={
                    "failure": "post_send_payment_state_unreadable",
                    "error": repr(exc),
                },
            )
            return False
        status = getattr(payment_state, "status", None)
        status = getattr(status, "value", status)
        return self._record_data_once(
            {"settlement_result": result, "post_send_payment_status": status},
            reason_code="payment_checkpoint_failed",
            failure="settlement_result_mismatch",
        )
```

Update `needs_settlement_read` in `_reconcile_verified_capture`:

```python
        needs_settlement_read = (
            self.pid_role.get(player_id) == ROLE_API
            and state.phase == PHASE_FUND_UPFRONT
            and (
                "settlement_result" not in state.data
                or "post_send_payment_status" not in state.data
            )
        )
```

The `attach`-time call `_reconcile_verified_capture(allow_settlement_read=False)` (:277) is unchanged — the deferred-read discipline now protects the fund capture instead of the acceptance capture.

- [ ] **Step 4: Run the test — PASS.**

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/live_gate_channels.py tests/arena/test_live_gate_channels.py
git commit -m "feat(arena): read settlement evidence in the funding seat window"
```

---

### Task 5: `_advance_after_capture` — verify enactment at the fund hop

**Files:**
- Modify: `src/civ_mcp/arena/live_gate_channels.py` — `_advance_after_capture` `PHASE_FUND_UPFRONT` branch (:1695–1723) and `PHASE_ACCEPT_UPFRONT_PAYMENT` branch (:1724 onward)
- Test: `tests/arena/test_live_gate_channels.py`

**Interfaces:**
- Consumes: `settlement_result`, `post_send_payment_status`, `settlement_baseline` from journal data (Task 4 and the existing write-ahead baseline at `seat_turn` :1056–1067).
- Produces: on the fund hop — `official_payment_not_enacted` terminal when `post_send_payment_status != "absent"` or deltas ≠ exactly (−PAYMENT_GOLD, +PAYMENT_GOLD); `settlement_digest` recorded in the fund hop; phase advances to `PHASE_ACCEPT_UPFRONT_PAYMENT`. On the acceptance hop — `SETTLED` ledger check, favor baseline/due-turn checks, and `settlement_digest` presence check remain; the delta arithmetic is deleted there. All checks remain pure reads of journal + canonical state.

- [ ] **Step 1: Write the failing tests**

```python
async def test_fund_hop_verifies_enactment_and_records_digest(...):
    # happy path: absent status + exact deltas -> digest recorded, phase advances
    ...
    data = driver._journal.state.data
    assert "settlement_digest" in data
    assert driver._journal.state.phase == lgc.PHASE_ACCEPT_UPFRONT_PAYMENT


async def test_fund_hop_fails_official_payment_not_enacted_on_pending_state(...):
    # post_send_payment_status "exact" -> terminal
    ...
    assert result["reason"] == "official_payment_not_enacted"


async def test_fund_hop_fails_official_payment_not_enacted_on_delta_mismatch(...):
    # payee treasury unchanged -> terminal
    ...
    assert result["reason"] == "official_payment_not_enacted"
```

Base these on the rev-2 lifecycle tests around the acceptance-hop delta verification (search `settlement_digest` in the test file) — the same arithmetic assertions move one hop earlier. Digest composition is unchanged: `_digest_mapping({"fingerprint": ..., "baseline": ..., "result": ...})`.

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement**

In the `PHASE_FUND_UPFRONT and role == ROLE_API` branch, after the existing `OFFERED` check and fingerprint/`payment_checkpoint_digest` recording and **before** `phase_advanced`: move the rev-2 delta-verification block (currently in the acceptance branch, :1746 onward) here, extended with the status check:

```python
            data = state.data
            status = data.get("post_send_payment_status")
            baseline = data.get("settlement_baseline")
            result = data.get("settlement_result")
            gold = PAYMENT_GOLD
            ok = (
                status == "absent"
                and isinstance(baseline, Mapping)
                and isinstance(result, Mapping)
                and result.get("turn") == baseline.get("turn") == turn
                and result.get("payer_gold") == baseline.get("payer_gold") - gold
                and result.get("payee_gold") == baseline.get("payee_gold") + gold
            )
            if not ok:
                self._fail(
                    "official_payment_not_enacted",
                    detail={
                        "status": status,
                        "baseline": baseline,
                        "result": result,
                    },
                )
                return
            if not self._record_data_once(
                {
                    "settlement_digest": self._digest_mapping(
                        {
                            "fingerprint": fingerprint,
                            "baseline": baseline,
                            "result": result,
                        }
                    )
                },
                reason_code="payment_checkpoint_failed",
                failure="settlement_digest_mismatch",
            ):
                return
```

Keep the delta arithmetic byte-identical to the rev-2 acceptance-branch version except for the added `status == "absent"` conjunct (compare against the current code at :1746–1830 before deleting it there). In the acceptance branch, replace the deleted arithmetic with a presence check:

```python
            if "settlement_digest" not in state.data:
                self._fail(
                    "payment_checkpoint_failed",
                    detail={"failure": "settlement_digest_missing_at_response"},
                )
                return
```

- [ ] **Step 4: Run the new tests — PASS.** Expect rev-2 lifecycle tests that asserted digest recording at the acceptance hop to fail; migrate them in Task 7 if they are schedule-wide, or inline here if they are single-purpose digest tests.

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/live_gate_channels.py tests/arena/test_live_gate_channels.py
git commit -m "feat(arena): verify synchronous enactment at the fund hop"
```

---

### Task 6: CLI pre-response check — absent expected, pending is the anomaly

**Files:**
- Modify: `src/civ_mcp/arena/live_gate_channels.py` — `seat_turn` CLI block (:1070–1116)
- Test: `tests/arena/test_live_gate_channels.py`

**Interfaces:**
- Consumes: journaled `pre_acceptance_payment_status` (mechanism unchanged, `_record_data_once` guarded).
- Produces: CLI response window proceeds only when the pre-response state is `absent`; `official_payment_unexpectedly_pending` terminal otherwise. The rev-2 test `test_absent_offer_at_acceptance_is_official_payment_auto_resolved` (or equivalent, search `auto_resolved` in the test file) is inverted.

- [ ] **Step 1: Write the failing tests**

```python
async def test_cli_response_proceeds_when_enacted_payment_absent(...):
    # pre-response state absent -> respond_to_payment plan emitted, phase advances
    ...


async def test_pending_offer_at_response_is_official_payment_unexpectedly_pending(...):
    # fake holds an exact pending offer at the CLI window -> terminal
    ...
    assert result["reason"] == "official_payment_unexpectedly_pending"
```

Delete/replace every `official_payment_auto_resolved` test (grep the test file; the forensic no longer exists).

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement** — in the `seat_turn` CLI block flip the expectation:

```python
                if status != "absent":
                    self._fail(
                        "official_payment_unexpectedly_pending",
                        detail={
                            "payer": self.role_pid[ROLE_API],
                            "payee": self.role_pid[ROLE_CLI],
                            "status": status,
                        },
                    )
                    return base_result
```

The journaling of `pre_acceptance_payment_status` via `_record_data_once` stays exactly as-is.

- [ ] **Step 4: Run the tests — PASS.**

- [ ] **Step 5: Commit**

```bash
git add src/civ_mcp/arena/live_gate_channels.py tests/arena/test_live_gate_channels.py
git commit -m "feat(arena): expect absent enacted payment at the CLI response window"
```

---

### Task 7: Engine-truth fakes and full-suite migration

**Files:**
- Modify: `tests/arena/live_gate_fakes.py` — `GateGameState.offer_channel_payment` (:68), `respond_to_channel_payment` (:84)
- Modify: `tests/arena/test_live_gate_channels.py` — remaining lifecycle expectations
- Test: full suite

**Interfaces:**
- Produces: the shared fake models the observed engine — `offer_channel_payment` enacts synchronously (moves gold, leaves nothing pending); `respond_to_channel_payment` raises `AssertionError` (nothing may call it). Every gate lifecycle test flows through this truth.

- [ ] **Step 1: Rewrite the fake engine**

```python
    async def offer_channel_payment(self, payee, gold):
        # Observed engine truth (2026-07-20 lifecycle probe): an AI->AI
        # PROPOSED deal is enacted synchronously at send -- the gold moves
        # before the first observable poll and nothing is ever pending.
        payer = self.active_player
        if (payer, payee) in self.pending:
            return "Error: CHANNEL_PAYMENT_PENDING_DEAL"
        self.treasury[payer] -= gold
        self.treasury[payee] += gold
        return "CHANNEL_PAYMENT_PROPOSED"

    async def respond_to_channel_payment(self, payer, gold, accept):
        raise AssertionError(
            "revision 3: nothing may mutate the engine at payment response"
        )
```

Keep `self.pending` and `get_channel_payment_state` unchanged — tests that need an anomalous pending offer (Task 5/6 terminals) seed `gs.pending[(payer, payee)]` directly.

- [ ] **Step 2: Run the gate suite and enumerate failures**

Run: `uv run pytest tests/arena/test_live_gate_channels.py -q 2>&1 | tail -20`
Fix each failing test against the revised phase-row expectations (Task 5/6 semantics, digest at the fund hop, absent at response). Restart-checkpoint and resume tests (rev-2 Tasks 5–6) should pass unchanged — they anchor on `settlement_digest` + absent state, both still recorded. Investigate any failure there as a regression, not a migration.

- [ ] **Step 3: Full suite**

Run: `uv run pytest tests -q`
Expected: green (≈1950+ tests).

- [ ] **Step 4: Commit**

```bash
git add tests/arena/live_gate_fakes.py tests/arena/test_live_gate_channels.py
git commit -m "test(arena): migrate gate suite to synchronous-enactment engine truth"
```

---

### Task 8: Experiment v4 and rerun readiness

**Files:**
- Modify: `experiments/arena-channels-core-smoke.yaml` (`run_id`)
- Test: `tests/arena/test_experiment.py` (run-id assertion)

**Steps:**

- [ ] **Step 1:** Update the run-id test first (search `arena-channels-core-gate-v3` in `tests/arena/test_experiment.py`), watch it fail, then set `run_id: arena-channels-core-gate-v4` with a comment: v3 failed terminally `official_payment_auto_resolved` (rev-2 forensic worked as designed); spec revision 3 responds with synchronous fund-window settlement; a failed gate journal is terminal so each rerun needs a fresh run_id.
- [ ] **Step 2:** `uv run pytest tests/arena/test_experiment.py tests/arena/test_config.py -q` — config validation accepts the 36 budgets against the computed 24 minimum.
- [ ] **Step 3:** Confirm no stale references: `grep -rn 'gate-v3\|auto_resolved' src tests experiments` → only historical mentions (retained run directories, spec/plan postmortem text) may remain; no live config/code/test may reference them as current.
- [ ] **Step 4: Commit** — `chore(arena): rev-3 rerun readiness (gate-v4)`.

**Live procedure after this plan (operator-attended, not part of the plan):** reload `CHANNELS_GATE_V1_T157` (manual menu load — no automation exists at the main menu on this rig), arm via the `civ6-arena-live` workflow with run `arena-channels-core-gate-v4`, two-invocation exit-75 handshake with fast rearm in the gap, expect terminal PASS at R8 (24 captures).

## Self-Review Notes

- Spec coverage: Locked Decision 14 → Tasks 1, 4, 5, 6, 7; revised `fund_upfront` row → Tasks 4–5; revised `accept_upfront_payment` row → Tasks 1, 6; fail-closed forensics `official_payment_not_enacted` / `official_payment_unexpectedly_pending` → Tasks 3, 5, 6; scenario-test obligation "payment enactment is verified synchronously … payee response settles the ledger exactly once with no engine mutation" → Tasks 1, 7 (the fake's `AssertionError` makes any engine response call a hard failure everywhere).
- The restart checkpoint (rev-2 Task 5) and resume verification (rev-2 Task 6) are deliberately untouched: both consume `settlement_digest` + absent payment-state, which rev 3 still records — Task 7 Step 2 treats failures there as regressions.
- Type consistency: `settlement_result` keeps the rev-2 shape `{"turn", "payer_gold", "payee_gold"}` (produced by the unchanged `_read_settlement_treasuries`); `post_send_payment_status` and `pre_acceptance_payment_status` are both plain strings extracted with the same `getattr(status, "value", status)` idiom; `ENACTED_ABSENT_RESULT` is defined once in Task 1 and consumed in Task 2.
- Fund-intent crash recovery deliberately stays fail-closed-ambiguous (Global Constraints): recovery observing `absent` without same-window deltas cannot distinguish enacted-then-crashed from never-sent across windows, and a gate rerun is cheaper than a wrong recovery.
