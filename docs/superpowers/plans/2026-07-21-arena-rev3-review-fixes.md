# Arena Rev-3 Review Fixes Implementation Plan

> **STATUS: EXECUTED AND LIVE-VALIDATED.** All 11 tasks shipped to main as
> `175100e..453a902` (13 commits, 2026-07-21, subagent-driven with per-task
> review; three adjudicated deviations: full `response_retry_*` family
> deletion in `ccbf62d`, dead `cleanup_deal` removal in `f62fb04`, and the
> `external_fund_settlement_verification` stand-down flag in `453a902`).
> Final whole-branch review: ready to merge, no Critical/Important findings;
> its deferred Minors shipped post-gate as `288cc0c`. The attended
> `arena-channels-core-gate-v4` run passed 2026-07-22 (evidence in
> `2026-07-16-arena-unofficial-channels-core-live-gate.md`, commit `3d51cdb`).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the 10 confirmed findings from the rev-3 synchronous-settlement code review (commits e7e0cb7..175100e) before the attended `arena-channels-core-gate-v4` live run.

**Architecture:** The rev-3 model stands (Locked Decision 14: the engine enacts AI→AI PROPOSED deals synchronously at send; post-send state is "absent" with gold already moved). Fixes fall into three groups: (a) fail-closed evidence handling in the live-gate driver (stale-turn reads, unreadable payment state, driver-vs-engine blame attribution), (b) rev-3 semantics completion in the channel runtime (fund-recovery inversion, ordinary-run settlement verification), and (c) dead-code deletion plus writer/validator deduplication so crash journals cannot self-poison.

**Tech Stack:** Python 3.12, pytest + pytest-asyncio, `uv` runner. No new dependencies.

## Global Constraints

- `SCENARIO_REVISION` stays **3**. Do not bump it.
- **No new public gate reason codes.** Reuse only registered codes: `payment_checkpoint_failed`, `payment_state_failed`, `official_payment_not_enacted`, `official_payment_unexpectedly_pending`, `preflight_failed`, `action_recovery_failed`, `channel_finish_failed`. New granularity goes in the `detail={"failure": ...}` string, never in the reason code (`tests/arena/test_live_gate_channels.py::test_revision_3_failure_codes_registered` pins the registry).
- **Locked Decision 14 governs:** a successful send is never observable as pending; an observed pending offer proves the payment was NOT enacted.
- **No delta-based fund recovery** (locked in the rev-3 spec): recovery paths stay fail-closed; treasury-delta verification happens only on live action paths.
- Full suite must stay green after every task: `uv run pytest tests -q` (1954 tests passing at HEAD 175100e; counts change as tests are added/removed).
- Commit after every task. Prefixes: `fix(arena):` for behavior, `refactor(arena):` for behavior-preserving consolidation, `docs(arena):` for doc-only changes.
- Line numbers below are anchored at HEAD `175100e` and shift as earlier tasks land — locate by the quoted code, not the number.

---

### Task 1: Delete the dead engine-mutation response path (finding 9)

Rev-3 forbids engine mutation at payment response; the production callers were removed but the method, its Lua builder, and their exports/tests survive.

**Files:**
- Modify: `src/civ_mcp/game_state.py:965-971` (delete `respond_to_channel_payment`)
- Modify: `src/civ_mcp/lua/channel_payments.py:283-298` (delete `build_channel_payment_response`), `:306` (`__all__` entry)
- Modify: `src/civ_mcp/lua/__init__.py:31` (delete re-export)
- Modify: `tests/test_channel_lua.py` (delete import at `:17`, the builder test at `:86`, the two injection param tuples at `:315-316`, and the `gs.respond_to_channel_payment` test at `:368`)
- Modify: `tests/arena/live_gate_fakes.py:87-90` (delete the raising tripwire method — with the production method gone it guards nothing)
- Modify: `tests/arena/test_channel_runtime.py:176-203` (delete `PaymentGameState.respond_to_channel_payment` and its now-unreferenced attributes `response_calls`, `response_results`, `yield_response`, `snapshot_at_response` — verify with grep first)

**Interfaces:**
- Consumes: nothing.
- Produces: nothing — pure deletion. Later tasks assume these symbols no longer exist.

- [ ] **Step 1: Confirm the code is dead**

Run: `rg -n "respond_to_channel_payment|build_channel_payment_response" src tests`
Expected: only the definition/export/fake/test sites listed above — no production caller.

- [ ] **Step 2: Delete the production method, builder, and exports**

Remove `GameState.respond_to_channel_payment` (game_state.py), `build_channel_payment_response` and its `__all__` entry (channel_payments.py), and the `build_channel_payment_response` line in `lua/__init__.py`.

- [ ] **Step 3: Delete the dead tests and fake methods**

In `tests/test_channel_lua.py`: remove the `build_channel_payment_response` import, its dedicated builder test, the two injection-test param tuples referencing it, and the `respond_to_channel_payment` GameState test. In `tests/arena/live_gate_fakes.py` delete the raising `respond_to_channel_payment`. In `tests/arena/test_channel_runtime.py`, run `rg -n "response_calls|response_results|yield_response|snapshot_at_response" tests/` — delete `PaymentGameState.respond_to_channel_payment` and each attribute with zero remaining references (keep any attribute another test still uses).

- [ ] **Step 4: Verify no references remain and the suite passes**

Run: `rg -n "respond_to_channel_payment|build_channel_payment_response" src tests` → no matches.
Run: `uv run pytest tests -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add -A src/civ_mcp tests
git commit -m "fix(arena): delete dead engine-mutation payment response path"
```

---

### Task 2: Shared settlement-read-pending predicate in the driver (finding 10)

`needs_settlement_read` in `_reconcile_verified_capture` is a hand-maintained De Morgan mirror of `_record_settlement_result`'s guard. Factor one predicate consulted by both.

**Files:**
- Modify: `src/civ_mcp/arena/live_gate_channels.py:1357-1369` and `:1435-1442`

**Interfaces:**
- Consumes: `self.pid_role`, `self._journal.state`, module constants `ROLE_API`, `PHASE_FUND_UPFRONT`.
- Produces: `ChannelsCoreDriver._settlement_read_pending(self, player_id: int) -> bool` — used by Task 5's test and by both call sites.

- [ ] **Step 1: Add the predicate and rewrite both sites**

Insert above `_record_settlement_result`:

```python
    def _settlement_read_pending(self, player_id: int) -> bool:
        """True while the API fund capture still owes its settlement read.

        Single source for _record_settlement_result and the attach-time
        reconcile: drift between the two either performs live reads in the
        read-free attach path or silently skips a required read.
        """
        state = self._journal.state
        return (
            self.pid_role.get(player_id) == ROLE_API
            and state.phase == PHASE_FUND_UPFRONT
            and (
                "settlement_result" not in state.data
                or "post_send_payment_status" not in state.data
            )
        )
```

In `_record_settlement_result`, replace the two leading `if` blocks (role/phase test and the two-key presence test) with:

```python
        if not self._settlement_read_pending(player_id):
            return True
```

In `_reconcile_verified_capture`, replace the `needs_settlement_read = (...)` expression with:

```python
        needs_settlement_read = self._settlement_read_pending(player_id)
```

- [ ] **Step 2: Run the driver suite (behavior-preserving refactor)**

Run: `uv run pytest tests/arena/test_live_gate_channels.py -q`
Expected: PASS, same test count.

- [ ] **Step 3: Commit**

```bash
git add src/civ_mcp/arena/live_gate_channels.py
git commit -m "refactor(arena): single settlement-read-pending predicate for record and reconcile"
```

---

### Task 3: Unreadable payment state is not "pending" (finding 5)

`get_channel_payment_state` returns `None` (no exception) on malformed FireTuner output. The driver journals `None` durably then blames the engine (`official_payment_unexpectedly_pending`); the runtime says "unexpectedly pending" for every unreadable shape.

**Files:**
- Modify: `src/civ_mcp/arena/live_gate_channels.py:1099-1116` (CLI pre-response), `:1388-1394` (post-send status record)
- Modify: `src/civ_mcp/arena/channel_runtime.py:2121-2124` (`_fund_deal` preflight), `:2268-2269` (`_respond_to_payment` preflight)
- Test: `tests/arena/test_live_gate_channels.py`, `tests/arena/test_channel_runtime.py`

**Interfaces:**
- Consumes: existing `_payment_state_status(payment_state, deal) -> str | None` (runtime), `_fail`/`_record_data_once` (driver).
- Produces: driver failure details `pre_acceptance_payment_state_unreadable` and `post_send_payment_state_unreadable` under the existing `payment_state_failed` reason code (the exception paths already use these labels — reuse them verbatim).

- [ ] **Step 1: Write the failing driver test**

Add to `tests/arena/test_live_gate_channels.py` (near `test_pre_acceptance_payment_state_query_failure_is_explicit`):

```python
@pytest.mark.asyncio
async def test_pre_acceptance_none_payment_state_is_unreadable_not_pending(
    tmp_path,
):
    gs = GateGameState()
    driver, runtime, gs = await attached_driver(tmp_path, gs=gs)
    await run_gate_round(driver, runtime, gs, 10)
    await run_gate_seat(driver, runtime, gs, 1, 11)

    async def unreadable(payer, payee, gold):
        return None

    gs.get_channel_payment_state = unreadable
    await run_gate_seat(driver, runtime, gs, 2, 11)

    state = driver._journal.state
    assert state.status == GATE_FAILED
    assert state.reason == "payment_state_failed"
    assert private_failure_details(driver)[-1]["failure"] == (
        "pre_acceptance_payment_state_unreadable"
    )
    assert "pre_acceptance_payment_status" not in state.data


@pytest.mark.asyncio
async def test_post_send_none_payment_state_is_unreadable_not_pending(tmp_path):
    gs = GateGameState()
    driver, runtime, gs = await attached_driver(tmp_path, gs=gs)
    await run_gate_round(driver, runtime, gs, 10)

    async def unreadable(payer, payee, gold):
        return None

    gs.get_channel_payment_state = unreadable
    await run_gate_seat(driver, runtime, gs, 1, 11)

    state = driver._journal.state
    assert state.status == GATE_FAILED
    assert state.reason == "payment_state_failed"
    assert private_failure_details(driver)[-1]["failure"] == (
        "post_send_payment_state_unreadable"
    )
    assert "post_send_payment_status" not in state.data
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/arena/test_live_gate_channels.py -k unreadable_not_pending -v`
Expected: FAIL — first test reaches `official_payment_unexpectedly_pending` (and journals `None`), second reaches `official_payment_not_enacted` at the advance step.

- [ ] **Step 3: Fix the driver**

In the CLI pre-response block (live_gate_channels.py, inside the `else:` that performs the live query), after the two `getattr` lines and **before** `_record_data_once`:

```python
                    status = getattr(payment_state, "status", None)
                    status = getattr(status, "value", status)
                    if status is None:
                        self._fail(
                            "payment_state_failed",
                            detail={
                                "failure": (
                                    "pre_acceptance_payment_state_unreadable"
                                ),
                                "state": repr(payment_state),
                            },
                        )
                        return base_result
```

Then, after the `if/else` that produced `status` (covering the journaled fast path too, so a poisoned pre-fix journal fails closed instead of blaming the engine), before `if status != "absent":`:

```python
                if status is None:
                    self._fail(
                        "payment_state_failed",
                        detail={
                            "failure": (
                                "pre_acceptance_payment_state_unreadable"
                            ),
                            "state": "journaled status is None",
                        },
                    )
                    return base_result
```

In `_record_settlement_result`, after the two `getattr` lines and before `_record_data_once`:

```python
        status = getattr(payment_state, "status", None)
        status = getattr(status, "value", status)
        if status is None:
            self._fail(
                "payment_state_failed",
                detail={
                    "failure": "post_send_payment_state_unreadable",
                    "state": repr(payment_state),
                },
            )
            return False
```

- [ ] **Step 4: Run driver tests**

Run: `uv run pytest tests/arena/test_live_gate_channels.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing runtime tests**

Add to `tests/arena/test_channel_runtime.py` (near the other respond-path tests; `payment_gs.state_results.append(None)` makes the fake return `None` once):

```python
@pytest.mark.asyncio
async def test_respond_rejects_unreadable_payment_state_as_unreadable(
    tmp_path, payment_gs
):
    rt, deal = await offered_payment_deal(tmp_path, payment_gs)
    payment_gs.state_results.append(None)
    ack = await apply_payment_action(
        rt,
        payment_gs,
        deal.counterparty,
        "respond_to_payment",
        {"deal_id": deal.id, "accept": True},
        turn=4,
    )
    assert ack.status == "rejected"
    assert "unreadable" in ack.message
    assert "unexpectedly pending" not in ack.message


@pytest.mark.asyncio
async def test_fund_rejects_unreadable_payment_state_as_unreadable(
    tmp_path, payment_gs
):
    rt, deal = await accepted_payment_deal(
        tmp_path, payment_gs, timing="up_front"
    )
    payment_gs.state_results.append(None)
    ack = await apply_payment_action(
        rt,
        payment_gs,
        deal.proposer,
        "fund_deal",
        {"deal_id": deal.id},
        turn=3,
    )
    assert ack.status == "rejected"
    assert "unreadable" in ack.message
```

(If the file has no `offered_payment_deal` helper, build the deal the same way the existing respond-path tests do — copy their setup verbatim.)

- [ ] **Step 6: Run to verify they fail, then fix the runtime**

Run: `uv run pytest tests/arena/test_channel_runtime.py -k unreadable -v` → FAIL with the wrong rejection messages.

In `_respond_to_payment` (channel_runtime.py), replace:

```python
        if self._payment_state_status(payment_state, deal) != "absent":
            raise _ActionRejected("the linked payment offer is unexpectedly pending")
```

with:

```python
        status = self._payment_state_status(payment_state, deal)
        if status is None:
            raise _ActionRejected(
                "could not verify the exact linked payment: state unreadable"
            )
        if status != "absent":
            raise _ActionRejected(
                "the linked payment offer is unexpectedly pending"
            )
```

In `_fund_deal`, replace:

```python
        if self._payment_state_status(payment_state, deal) != "absent":
            raise _ActionRejected(
                "the ordered pair already has a pending or conflicting deal"
            )
```

with:

```python
        status = self._payment_state_status(payment_state, deal)
        if status is None:
            raise _ActionRejected(
                "could not verify the ordered-pair payment state: "
                "state unreadable"
            )
        if status != "absent":
            raise _ActionRejected(
                "the ordered pair already has a pending or conflicting deal"
            )
```

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest tests -q` → PASS.

- [ ] **Step 8: Commit**

```bash
git add src/civ_mcp/arena/live_gate_channels.py src/civ_mcp/arena/channel_runtime.py tests/arena
git commit -m "fix(arena): treat unreadable payment state as unreadable, not pending"
```

---

### Task 4: Split fund-hop verification — driver defects vs engine non-enactment (finding 6)

The single `ok` conjunct folds driver-internal evidence defects into the engine-blame terminal `official_payment_not_enacted`. Extract a pure classifier so each arm is unit-testable and correctly attributed.

**Files:**
- Modify: `src/civ_mcp/arena/live_gate_channels.py:1744-1793` (the fund-hop verification in `_advance_after_capture`)
- Test: `tests/arena/test_live_gate_channels.py`

**Interfaces:**
- Consumes: `Mapping` (already imported in the module), `PAYMENT_GOLD`.
- Produces: `ChannelsCoreDriver._settlement_verdict(status, baseline, result, recorded, turn, gold) -> str | None` (staticmethod) — returns `None` (verifiably enacted), `"evidence_invalid"` (driver-attributed), or `"not_enacted"` (engine-attributed).

- [ ] **Step 1: Write the failing unit tests for the classifier**

```python
def test_settlement_verdict_classifies_evidence_and_enactment():
    verdict = lgc.ChannelsCoreDriver._settlement_verdict
    fingerprint = exact_payment_fingerprint()
    baseline = {"turn": 11, "payer_gold": 500, "payee_gold": 500}
    good = {"turn": 11, "payer_gold": 499, "payee_gold": 501}
    gold = lgc.PAYMENT_GOLD

    assert verdict("absent", baseline, good, fingerprint, 11, gold) is None
    # engine-attributed: pending state, or well-formed evidence w/o transfer
    assert verdict("exact", baseline, good, fingerprint, 11, gold) == (
        "not_enacted"
    )
    no_move = {"turn": 11, "payer_gold": 500, "payee_gold": 500}
    assert verdict("absent", baseline, no_move, fingerprint, 11, gold) == (
        "not_enacted"
    )
    # driver-attributed: malformed or cross-turn evidence
    assert verdict(None, baseline, good, fingerprint, 11, gold) == (
        "evidence_invalid"
    )
    assert verdict("absent", None, good, fingerprint, 11, gold) == (
        "evidence_invalid"
    )
    assert verdict("absent", baseline, good, None, 11, gold) == (
        "evidence_invalid"
    )
    stale = {"turn": 10, "payer_gold": 499, "payee_gold": 501}
    assert verdict("absent", baseline, stale, fingerprint, 11, gold) == (
        "evidence_invalid"
    )
    floats = {"turn": 11, "payer_gold": 499.0, "payee_gold": 501}
    assert verdict("absent", baseline, floats, fingerprint, 11, gold) == (
        "evidence_invalid"
    )
```

Run: `uv run pytest tests/arena/test_live_gate_channels.py -k settlement_verdict -v` → FAIL (`AttributeError`).

- [ ] **Step 2: Implement the classifier and rewire the fund hop**

Add to `ChannelsCoreDriver`:

```python
    @staticmethod
    def _settlement_verdict(status, baseline, result, recorded, turn, gold):
        """Classify fund-hop settlement evidence.

        None: verifiably enacted. "not_enacted": well-formed evidence shows
        the engine did not enact (engine-attributed). "evidence_invalid":
        the driver's own evidence is malformed or cross-turn
        (driver-attributed) — never blamed on the engine.
        """
        if status is None:
            return "evidence_invalid"
        if status != "absent":
            return "not_enacted"
        for values in (baseline, result):
            if not (
                isinstance(values, Mapping)
                and values.get("turn") == turn
                and type(values.get("payer_gold")) is int
                and type(values.get("payee_gold")) is int
            ):
                return "evidence_invalid"
        if recorded is None:
            return "evidence_invalid"
        if (
            result["payer_gold"] == baseline["payer_gold"] - gold
            and result["payee_gold"] == baseline["payee_gold"] + gold
        ):
            return None
        return "not_enacted"
```

In `_advance_after_capture`'s fund hop, delete the four `baseline_payer_gold`/... extractions and the `ok = (...)` block plus its `if not ok:` failure, replacing them with:

```python
            verdict = self._settlement_verdict(
                status, baseline, result, recorded, turn, gold
            )
            if verdict == "evidence_invalid":
                self._fail(
                    "payment_checkpoint_failed",
                    detail={
                        "failure": "settlement_evidence_invalid",
                        "status": status,
                        "baseline": baseline,
                        "result": result,
                        "recorded": recorded,
                        "turn": turn,
                    },
                )
                return
            if verdict == "not_enacted":
                self._fail(
                    "official_payment_not_enacted",
                    detail={
                        "status": status,
                        "baseline": baseline,
                        "result": result,
                        "recorded": recorded,
                        "turn": turn,
                    },
                )
                return
```

- [ ] **Step 3: Run driver tests**

Run: `uv run pytest tests/arena/test_live_gate_channels.py -q`
Expected: PASS — the existing pending-state and no-transfer tests still land on `official_payment_not_enacted` (their details gain `recorded` and `turn` keys; the delta-mismatch test asserts only `baseline`/`result` so it still passes).

- [ ] **Step 4: Full suite, then commit**

Run: `uv run pytest tests -q` → PASS.

```bash
git add src/civ_mcp/arena/live_gate_channels.py tests/arena/test_live_gate_channels.py
git commit -m "fix(arena): attribute malformed settlement evidence to the driver, not the engine"
```

---

### Task 5: Live-turn guard on settlement treasury reads (finding 1)

The deferred crash-recovery settlement read stamps live treasuries with the stored capture turn — falsifying evidence and enabling either a false engine forensic or a false PASS. Read the live game turn on every settlement read and fail closed on drift.

**Files:**
- Modify: `src/civ_mcp/game_state.py` (new `get_current_game_turn`)
- Modify: `src/civ_mcp/arena/live_gate_channels.py:2356-2379` (`_read_settlement_treasuries`)
- Modify: `tests/arena/live_gate_fakes.py` (`GateGameState.game_turn` + `get_current_game_turn`; `run_gate_seat` stamps it)
- Test: `tests/arena/test_live_gate_channels.py`

**Interfaces:**
- Consumes: `self.conn.execute_read` (GameState), `_fail` (driver).
- Produces: `GameState.get_current_game_turn(self) -> int`; fake `GateGameState.get_current_game_turn(self) -> int` returning `self.game_turn`; `run_gate_seat` sets `gs.game_turn = turn` next to `gs.active_player = pid`.

- [ ] **Step 1: Write the failing drift test**

Add to `tests/arena/test_live_gate_channels.py` (modeled on `test_settlement_result_append_crash_reconstructs_normal_capture`, but crashing **before** the settlement append so no settlement data is durable, then advancing the game turn before resume):

```python
@pytest.mark.asyncio
async def test_deferred_settlement_read_after_turn_advance_fails_closed(
    tmp_path,
):
    class SimulatedCrash(BaseException):
        pass

    gs = GateGameState()
    driver, runtime, gs = await attached_driver(tmp_path, gs=gs)
    await run_gate_round(driver, runtime, gs, 10)

    journal = driver._journal
    real_append = journal.append

    def crashing_append(kind, payload):
        if kind == "data_recorded" and "settlement_result" in payload["data"]:
            raise SimulatedCrash()
        return real_append(kind, payload)

    journal.append = crashing_append
    with pytest.raises(SimulatedCrash):
        await run_gate_seat(driver, runtime, gs, 1, 11)
    assert len(data_events_for_key(driver, "settlement_result")) == 0

    resumed = lgc.ChannelsCoreDriver(gate_config())
    await resumed.attach(
        gs=gs, channel_runtime=runtime, run_dir=driver._run_dir
    )
    assert resumed.pending_signal() is None  # attach performs no reads

    # The human ends the turn before the watcher is rearmed.
    await run_gate_seat(resumed, runtime, gs, 2, 12)

    state = resumed._journal.state
    assert state.status == GATE_FAILED
    assert state.reason == "payment_checkpoint_failed"
    assert private_failure_details(resumed)[-1] == {
        "failure": "settlement_read_turn_drift",
        "capture_turn": 11,
        "live_turn": 12,
    }
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/arena/test_live_gate_channels.py -k turn_advance_fails_closed -v`
Expected: FAIL — today the deferred read succeeds and stamps turn-11 evidence from a turn-12 game (the test may currently die later with a different failure; the point is it does not produce `settlement_read_turn_drift`).

- [ ] **Step 3: Add `GameState.get_current_game_turn`**

In `src/civ_mcp/game_state.py` (next to `get_channel_payment_state`; same pattern as `end_turn.py:_get_turn_number`):

```python
    async def get_current_game_turn(self) -> int:
        lines = await self.conn.execute_read(
            'print(Game.GetCurrentGameTurn()); print("---END---")'
        )
        if not lines:
            raise ValueError("no response to game turn query")
        return int(lines[0])
```

- [ ] **Step 4: Guard `_read_settlement_treasuries`**

Prepend to the method body (before the `values = {}` loop):

```python
        try:
            live_turn = await gs.get_current_game_turn()
        except Exception as exc:
            self._fail(
                "payment_state_failed",
                detail={
                    "failure": "settlement_turn_unreadable",
                    "error": repr(exc),
                },
            )
            return None
        if live_turn != turn:
            self._fail(
                "payment_checkpoint_failed",
                detail={
                    "failure": "settlement_read_turn_drift",
                    "capture_turn": turn,
                    "live_turn": live_turn,
                },
            )
            return None
```

- [ ] **Step 5: Teach the fakes and harness about the live turn**

In `tests/arena/live_gate_fakes.py`, add to `GateGameState.__init__`: `self.game_turn = 0`, and the method:

```python
    async def get_current_game_turn(self):
        return self.game_turn
```

In `run_gate_seat`, next to `gs.active_player = pid`, add: `gs.game_turn = turn`.

- [ ] **Step 6: Run the driver suite; fix manual-seat tests**

Run: `uv run pytest tests/arena/test_live_gate_channels.py -q`
Any test that drives a **fund seat** manually (calling `admit_player`/`finish_player`/`after_seat_capture` without `run_gate_seat`) needs `gs.game_turn = <turn>` before the settlement read fires. Grep with `rg -n "after_seat_capture" tests/arena/test_live_gate_channels.py` and stamp the turn in those setups.
Expected: PASS including the new drift test.

- [ ] **Step 7: Full suite, then commit**

Run: `uv run pytest tests -q` → PASS.

```bash
git add src/civ_mcp/game_state.py src/civ_mcp/arena/live_gate_channels.py tests/arena
git commit -m "fix(arena): fail closed when a settlement read spans a game-turn advance"
```

---

### Task 6: Fund-intent recovery — a pending exact offer means NOT enacted (finding 2)

The fund-recovery `'exact'` branch still encodes rev-2 semantics (pending offer ⇒ recovered success). Under Locked Decision 14 a pending offer proves non-enactment: fold `'exact'` into the existing `'conflicting'` handling (funding stays due until the deadline, then breach).

**Files:**
- Modify: `src/civ_mcp/arena/channel_runtime.py:2595-2612` (writer), `:1206-1209` (validator branch `observed_exact_offer` — delete; no journal on disk uses it, verified 2026-07-21)
- Test: `tests/arena/test_channel_runtime.py:2916-2956` (rewrite), plus a new past-deadline variant

**Interfaces:**
- Consumes: existing `_commit_payment_result`, `_broken_deal_records`, engine results `RECOVERY_CONFLICTING_PAYMENT` / `RECOVERY_CONFLICTING_PAYMENT_LATE`, recovery kinds `conflicting_offer` / `conflicting_offer_after_deadline`.
- Produces: no new identifiers — `RECOVERED_EXACT_CHANNEL_PAYMENT` and `observed_exact_offer` cease to exist.

- [ ] **Step 1: Rewrite the pinned rev-2 test into the rev-3 expectation**

Replace `test_recovery_observed_offer_records_offered_without_resend` (tests/arena/test_channel_runtime.py:2916) with:

```python
@pytest.mark.asyncio
async def test_recovery_exact_pending_offer_leaves_funding_due(
    tmp_path,
    payment_gs,
):
    rt, deal = await accepted_payment_deal(
        tmp_path,
        payment_gs,
        timing="up_front",
    )
    rt._commit(
        "payment_fund_intent",
        payment_fund_intent_payload(deal, "src-fund-recovery"),
    )
    payment_gs.install_exact_offer(
        deal.proposer,
        deal.counterparty,
        deal.payment_gold,
    )
    payment_gs.local_player = deal.counterparty
    reopened = runtime(tmp_path)
    await reopened.reconcile_payment_intents(
        payment_gs, current_turn=3, current_player_id=deal.counterparty
    )
    # Rev-3: a pending offer proves the send did not enact — funding
    # remains due; nothing is recovered as OFFERED.
    recovered = reopened.deal(deal.id)
    assert recovered.state is DealState.ACTIVE
    assert recovered.payment_status is PaymentStatus.DUE
    assert payment_gs.offer_calls == 0
    assert "src-fund-recovery" in reopened.state.applied_source_ids


@pytest.mark.asyncio
async def test_recovery_exact_pending_offer_after_deadline_is_funding_breach(
    tmp_path,
    payment_gs,
):
    rt, deal = await accepted_payment_deal(
        tmp_path,
        payment_gs,
        timing="up_front",
    )
    rt._commit(
        "payment_fund_intent",
        payment_fund_intent_payload(deal, "src-fund-late"),
    )
    payment_gs.install_exact_offer(
        deal.proposer,
        deal.counterparty,
        deal.payment_gold,
    )
    payment_gs.local_player = deal.counterparty
    reopened = runtime(tmp_path)
    intent = next(
        intent
        for _, intent in reopened._unfinished_payment_intents()
        if intent["source_id"] == "src-fund-late"
    )
    await reopened.reconcile_payment_intents(
        payment_gs,
        current_turn=intent["deadline"] + 1,
        current_player_id=deal.counterparty,
    )
    assert reopened.deal(deal.id).state is DealState.BROKEN
    assert reopened.state.grievances[-1].wronged == deal.counterparty
```

Match the assertions on `PaymentStatus.DUE`/breach to what the existing `conflicting` tests (`test_recovery_conflicting_offer_retains_open_funding_obligation` and its late variant) assert — copy their exact expectations if they differ in detail.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/arena/test_channel_runtime.py -k exact_pending_offer -v`
Expected: FAIL — current code recovers the payment as `OFFERED`.

- [ ] **Step 3: Fix the writer**

In `reconcile_payment_intents` (channel_runtime.py), replace the `if status == "exact":` block (the `RECOVERED_EXACT_CHANNEL_PAYMENT` commit) and its `elif status == "conflicting":` header so both statuses share the conflicting handling:

```python
            if kind == "payment_fund_intent":
                # Rev-3 (Locked Decision 14): a successful send is never
                # observable as pending, so any pending offer — exact or
                # conflicting — proves the payment was not enacted.
                if status in ("exact", "conflicting"):
                    if current_turn <= intent["deadline"]:
                        self._commit_payment_result(
                            "payment_fund_result",
                            intent,
                            engine_result="RECOVERY_CONFLICTING_PAYMENT",
                            recovery="conflicting_offer",
                            deal=None,
                            grievance=None,
                            message=(
                                "pending payment offer left funding due for "
                                f"{deal.id}"
                            ),
                        )
                    else:
                        broken, grievance = self._broken_deal_records(
                            deal,
                            turn=current_turn,
                            breach="funding",
                            reason=(
                                "linked payment was not funded by the deadline"
                            ),
                        )
                        self._commit_payment_result(
                            "payment_fund_result",
                            intent,
                            engine_result="RECOVERY_CONFLICTING_PAYMENT_LATE",
                            recovery="conflicting_offer_after_deadline",
                            deal=broken,
                            grievance=grievance,
                            message=f"payment funding breached for {deal.id}",
                        )
                else:
                    ...  # existing absent -> RECOVERY_PAYMENT_ABSENT branch, unchanged
                continue
```

(Keep the existing absent/unverifiable branch byte-for-byte; only the exact/conflicting restructure changes.)

- [ ] **Step 4: Delete the dead validator branch**

In `_validate_payment_result_semantics`, delete the `elif recovery == "observed_exact_offer":` arm entirely. Run `rg -n "RECOVERED_EXACT_CHANNEL_PAYMENT|observed_exact_offer" src tests` and remove any remaining test references.

- [ ] **Step 5: Full suite, then commit**

Run: `uv run pytest tests -q` → PASS.

```bash
git add src/civ_mcp/arena/channel_runtime.py tests/arena/test_channel_runtime.py
git commit -m "fix(arena): fund recovery treats a pending exact offer as not enacted"
```

---

### Task 7: Delete dead legacy response replay branches; correct the plan doc (finding 4)

Legacy rev-1/rev-2 response journals are hard-rejected at `open` (pinned by `test_open_rejects_payment_response_intent_with_legacy_exact_preflight`), so the retained legacy response-result branches are unreachable, and the rev-3 plan's compatibility claim is false. No journal on disk contains a `payment_response_intent` (verified 2026-07-21).

**Files:**
- Modify: `src/civ_mcp/arena/channel_runtime.py:1244-1350` (validator response subtree), `:2374-2383` (`_response_succeeded` — delete)
- Modify: `docs/superpowers/plans/2026-07-20-arena-live-gate-synchronous-settlement.md:487`
- Test: `tests/arena/test_channel_runtime.py` (remove any tests pinning the deleted branches)

**Interfaces:**
- Consumes: `ENACTED_ABSENT_RESULT`.
- Produces: the validator's response subtree accepts exactly two recovery kinds: `None`/`"observed_absent_enacted"` (requiring `ENACTED_ABSENT_RESULT`) and `"conflicting_offer"` (requiring `RECOVERY_PAYMENT_UNEXPECTEDLY_PENDING`). Task 9 merges the first pair's bodies.

- [ ] **Step 1: Confirm the branches are unreachable**

Run: `rg -n "response_retried|response_retry_query_failed" src tests` — no writer produces them (only the validator + any pinning tests).
Run: `rg -rln "payment_response_intent" arena_runs/ .arena-runs/` — no matches.

- [ ] **Step 2: Rewrite the validator response subtree**

In `_validate_payment_result_semantics` (the `else:` for response results):

1. Delete `engine_accept = False if cleanup is not None else accept`.
2. Change `if recovery in {None, "response_retried"}:` to `if recovery is None:` and replace its body's success test with a hard requirement — the branch becomes:

```python
            if recovery is None:
                if engine_result != ENACTED_ABSENT_RESULT:
                    raise ValueError(
                        "invalid ledger-only payment response result"
                    )
                if cleanup is not None:
                    observation_id = intent.get("observation_id")
                    expected_deal = cls._unverifiable_deal_record(
                        deal,
                        turn=intent["turn"],
                        reason=_INCOMPLETE_ACCEPTANCE_REASON,
                        evidence_refs=(
                            (observation_id,)
                            if isinstance(observation_id, str)
                            else ()
                        ),
                    )
                elif accept:
                    expected_deal = cls._validated_success_deal(
                        state, deal, intent
                    )
                else:
                    expected_deal, expected_grievance = (
                        cls._expected_breach_records(
                            state,
                            deal,
                            turn=intent["turn"],
                            breach="payment_response",
                            reason="enacted linked payment was rejected",
                        )
                    )
```

   (This drops the `"exact linked payment was rejected"` legacy reason ternary and the `_authoritative_payment_failure` arm — no rev-3 writer produces either.)
3. In the `elif recovery == "conflicting_offer":` arm, require exactly `RECOVERY_PAYMENT_UNEXPECTEDLY_PENDING` (drop `RECOVERY_PAYMENT_ABSENT` from the accepted set — response writers never emit it; the **fund**-side `offer_absent` branch that legitimately uses `RECOVERY_PAYMENT_ABSENT` is separate and stays) and drop the legacy base reason `"...the exact offer is conflicting"` from the reasons set, keeping only the two `"unexpectedly ..."` members.
4. Delete the response-side `elif recovery == "offer_absent":` and `elif recovery == "response_retry_query_failed":` arms.
5. Delete `_response_succeeded` (now uncalled — verify with `rg -n "_response_succeeded" src tests`).

- [ ] **Step 3: Fix fallout tests**

Run: `uv run pytest tests/arena/test_channel_runtime.py -q`. Any test that constructs a `payment_response_result` with `response_retried`, `response_retry_query_failed`, response-side `offer_absent`, legacy `CHANNEL_PAYMENT_ACCEPTED/REJECTED` engine results, or the legacy reasons is pinning deleted behavior — delete or rewrite it to the rev-3 shape (`ENACTED_ABSENT_RESULT` / the two surviving recovery kinds).

- [ ] **Step 4: Correct the plan document**

In `docs/superpowers/plans/2026-07-20-arena-live-gate-synchronous-settlement.md`, replace line 487:

```
Keep legacy `"offer_absent"` replay support for historical rev-1/rev-2 response journals.
```

with:

```
Legacy rev-1/rev-2 response journals are rejected at `ChannelRuntime.open` (their response intents recorded preflight `"exact"`, which rev-3 validation refuses — pinned by `test_open_rejects_payment_response_intent_with_legacy_exact_preflight`). There is deliberately no legacy response replay support; fund-side `"offer_absent"` recovery is unrelated and remains live.
```

- [ ] **Step 5: Full suite, then commit (code and docs separately)**

Run: `uv run pytest tests -q` → PASS.

```bash
git add src/civ_mcp/arena/channel_runtime.py tests/arena/test_channel_runtime.py
git commit -m "fix(arena): delete unreachable legacy payment-response replay branches"
git add docs/superpowers/plans/2026-07-20-arena-live-gate-synchronous-settlement.md
git commit -m "docs(arena): correct rev-3 plan claim about legacy response journal support"
```

---

### Task 8: Shared reason source for unexpectedly-pending validation (finding 7)

The `conflicting_offer` replay branch hand-enumerates literal expansions of the writer's f-string. Wording drift would make the runtime write journals its own validator rejects. Use one builder for both, like the existing `_INCOMPLETE_ACCEPTANCE_REASON` mechanism.

**Files:**
- Modify: `src/civ_mcp/arena/channel_runtime.py` (module constants near `:60`; writer near `:2663-2670`; validator `conflicting_offer` arm)
- Test: `tests/arena/test_channel_runtime.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: module-level `_response_pending_reason(status: str) -> str` and `_RESPONSE_PENDING_REASONS: frozenset[str]`.

- [ ] **Step 1: Write the failing round-trip test**

```python
@pytest.mark.asyncio
async def test_pending_response_recovery_reason_survives_replay(
    tmp_path, payment_gs
):
    rt, deal = await offered_payment_deal(tmp_path, payment_gs)
    rt._commit(
        "payment_response_intent",
        payment_response_intent_payload(deal, "src-resp-pending", accept=True),
    )
    payment_gs.pending[(deal.proposer, deal.counterparty)] = (
        ExactPaymentOffer(
            deal.proposer, deal.counterparty, deal.payment_gold + 1
        )
    )
    payment_gs.local_player = deal.counterparty
    reopened = runtime(tmp_path)
    await reopened.reconcile_payment_intents(
        payment_gs, current_turn=4, current_player_id=deal.counterparty
    )
    assert reopened.deal(deal.id).state is DealState.UNVERIFIABLE
    # The written journal must replay through the validator.
    replayed = runtime(tmp_path)
    assert replayed.deal(deal.id).state is DealState.UNVERIFIABLE
```

(Reuse the file's existing helpers for building an offered deal and a response-intent payload — copy the setup from the nearest existing response-recovery test if the helper names differ.) This passes today; it is the regression net. The unit half that fails first:

```python
def test_response_pending_reasons_are_shared_with_the_writer():
    from civ_mcp.arena import channel_runtime as cr

    assert cr._response_pending_reason("exact") in (
        cr._RESPONSE_PENDING_REASONS
    )
    assert cr._response_pending_reason("conflicting") in (
        cr._RESPONSE_PENDING_REASONS
    )
    assert len(cr._RESPONSE_PENDING_REASONS) == 2
```

Run: `uv run pytest tests/arena/test_channel_runtime.py -k pending_reason -v` → the unit test FAILS (`AttributeError`).

- [ ] **Step 2: Implement the shared source**

Below `ENACTED_ABSENT_RESULT` in channel_runtime.py:

```python
def _response_pending_reason(status: str) -> str:
    """Single source for the writer and the replay validator — the journal
    self-poisons if the two ever drift."""
    return (
        "payment response intent outcome is ambiguous because an offer is "
        f"unexpectedly {status}"
    )


_RESPONSE_PENDING_REASONS = frozenset(
    _response_pending_reason(status) for status in ("exact", "conflicting")
)
```

Writer (`reconcile_payment_intents`, the `status != "absent"` response branch): replace the inline f-string reason with `reason=_response_pending_reason(status)`.

Validator (`conflicting_offer` arm, after Task 7): replace the literal `reasons` set with `_RESPONSE_PENDING_REASONS`.

- [ ] **Step 3: Run and commit**

Run: `uv run pytest tests -q` → PASS.

```bash
git add src/civ_mcp/arena/channel_runtime.py tests/arena/test_channel_runtime.py
git commit -m "refactor(arena): share unexpectedly-pending reasons between writer and validator"
```

---

### Task 9: One source for absent-response outcome records (finding 8)

The cleanup/accept/reject outcome subtree exists in three hand-maintained copies (direct path, recovery, replay validator) with the breach reason as a bare literal ×4. Drift makes crash-recovered journals fail replay. Consolidate.

**Files:**
- Modify: `src/civ_mcp/arena/channel_runtime.py` — new constant + classmethod; rewire `:2288-2323` (direct), `:2682-2734` (recovery), and the validator branches from Task 7.

**Interfaces:**
- Consumes: `_unverifiable_deal_record`, `_validated_success_deal`, `_expected_breach_records`, `_INCOMPLETE_ACCEPTANCE_REASON`.
- Produces: `_ENACTED_PAYMENT_REJECTED_REASON = "enacted linked payment was rejected"` (module constant) and `ChannelRuntime._absent_response_records(cls, state, deal, intent) -> tuple[Deal, dict | None, str]` where the third element is `"cleanup" | "accepted" | "rejected"`.

- [ ] **Step 1: Implement the shared helper**

Module constant next to `ENACTED_ABSENT_RESULT`:

```python
_ENACTED_PAYMENT_REJECTED_REASON = "enacted linked payment was rejected"
```

Classmethod (near `_expected_breach_records`):

```python
    @classmethod
    def _absent_response_records(
        cls, state: ChannelState, deal: Deal, intent: dict
    ) -> tuple[Deal, dict | None, str]:
        """Outcome records for a ledger-only response to an absent enacted
        payment (rev-3). Single source for the direct path, recovery, and
        replay validation — drift between them makes recovered journals
        fail replay."""
        if intent.get("cleanup") is not None:
            observation_id = intent.get("observation_id")
            return (
                cls._unverifiable_deal_record(
                    deal,
                    turn=intent["turn"],
                    reason=_INCOMPLETE_ACCEPTANCE_REASON,
                    evidence_refs=(
                        (observation_id,)
                        if isinstance(observation_id, str)
                        else ()
                    ),
                ),
                None,
                "cleanup",
            )
        if intent["accept"]:
            return (
                cls._validated_success_deal(state, deal, intent),
                None,
                "accepted",
            )
        expected_deal, grievance = cls._expected_breach_records(
            state,
            deal,
            turn=intent["turn"],
            breach="payment_response",
            reason=_ENACTED_PAYMENT_REJECTED_REASON,
        )
        return expected_deal, grievance, "rejected"
```

(`_broken_deal_records(self, ...)` is exactly `_canonical_breach_records(self.state, ...)`, which `_expected_breach_records` also wraps — the substitution is equivalence-by-construction; `_commit_payment_result` already validates every commit against the validator's expectation, so any real divergence fails loudly in tests.)

- [ ] **Step 2: Rewire the direct path**

Replace the three commit arms after `engine_result = ENACTED_ABSENT_RESULT` in `_respond_to_payment` (keep the pre-intent `success_deal`/`cleanup_deal` computation at `:2236-2256` — it feeds the intent payload):

```python
        engine_result = ENACTED_ABSENT_RESULT
        expected_deal, grievance, outcome = self._absent_response_records(
            self.state, deal, intent
        )
        message = {
            "cleanup": f"payment acceptance became unverifiable for {deal.id}",
            "accepted": f"accepted linked payment for {deal.id}",
            "rejected": f"rejected linked payment for {deal.id}",
        }[outcome]
        return self._commit_payment_result(
            "payment_response_result",
            intent,
            engine_result=engine_result,
            recovery=None,
            deal=expected_deal,
            grievance=grievance,
            message=message,
        )
```

- [ ] **Step 3: Rewire the recovery path**

Replace the cleanup/accept/reject subtree at the end of `reconcile_payment_intents` (everything from `accept = intent["accept"]` through the final `_commit_payment_result`):

```python
            expected_deal, grievance, outcome = self._absent_response_records(
                self.state, deal, intent
            )
            message = {
                "cleanup": (
                    f"recovered incomplete payment acceptance for {deal.id}"
                ),
                "accepted": f"recovered accepted payment for {deal.id}",
                "rejected": f"recovered rejected payment for {deal.id}",
            }[outcome]
            self._commit_payment_result(
                "payment_response_result",
                intent,
                engine_result=ENACTED_ABSENT_RESULT,
                recovery="observed_absent_enacted",
                deal=expected_deal,
                grievance=grievance,
                message=message,
            )
```

- [ ] **Step 4: Merge the validator branches**

After Task 7 the `recovery is None` and `recovery == "observed_absent_enacted"` validator bodies are identical. Merge into:

```python
            if recovery in (None, "observed_absent_enacted"):
                if engine_result != ENACTED_ABSENT_RESULT:
                    raise ValueError(
                        "invalid ledger-only payment response result"
                    )
                expected_deal, expected_grievance, _ = (
                    cls._absent_response_records(state, deal, intent)
                )
```

Also replace the remaining `"enacted linked payment was rejected"` literals anywhere in the file with `_ENACTED_PAYMENT_REJECTED_REASON` (`rg -n '"enacted linked payment was rejected"' src` → zero matches afterward).

- [ ] **Step 5: Run and commit**

This is behavior-preserving; the existing direct/recovery/replay tests are the net.

Run: `uv run pytest tests -q` → PASS.

```bash
git add src/civ_mcp/arena/channel_runtime.py
git commit -m "refactor(arena): single source for absent-response outcome records"
```

---

### Task 10: Ordinary-run fund settlement verification (finding 3)

Non-gate channel runs (coordinator opens `ChannelRuntime` with `live_gate_driver=None`) currently settle on "absent" alone — a silent no-transfer send yields a phantom SETTLED/HONORED. Verify enactment in `_fund_deal` itself: treasuries before send, treasuries + payment state after; report verifiable non-enactment through the existing authoritative-failure path.

**Files:**
- Modify: `src/civ_mcp/arena/channel_runtime.py` (`_fund_deal` `:2110-2180`, two new helpers, imports)
- Modify: `tests/arena/test_channel_runtime.py` (`PaymentGameState` — rev-3 synchronous enactment model)
- Test: `tests/arena/test_channel_runtime.py`

**Interfaces:**
- Consumes: `gs.get_channel_observation(pid, turn, request)`, `ObservationFamily`/`ObservationRequest` from `civ_mcp.arena.channel_terms` (add the import), `_payment_state_status`.
- Produces: `_read_payment_treasuries(self, gs, deal, turn) -> dict[str, int] | None`; `_verify_synchronous_settlement(self, gs, deal, turn, baseline) -> bool | None`; new authoritative engine result string `"Error: CHANNEL_PAYMENT_NOT_ENACTED"` (already accepted by the validator via `_authoritative_payment_failure`, so no validator change).

- [ ] **Step 1: Update `PaymentGameState` to the rev-3 synchronous model**

In `tests/arena/test_channel_runtime.py`:

Add to `__init__`: `self.treasury = {pid: 500 for pid in range(8)}`.

In `offer_channel_payment`, replace the success-path `install_exact_offer` call (`if result == "CHANNEL_PAYMENT_PROPOSED": self.install_exact_offer(...)`) with synchronous enactment:

```python
        if result == "CHANNEL_PAYMENT_PROPOSED":
            self.treasury[self.local_player] -= gold
            self.treasury[payee] += gold
        return result
```

Add (importing `observation` from `.live_gate_fakes`):

```python
    async def get_channel_observation(self, player_id, turn, request):
        return observation(
            player_id,
            turn,
            treasury_gold=self.treasury.get(player_id, 500),
        )
```

`install_exact_offer` stays for tests that explicitly plant a stray pending offer.

- [ ] **Step 2: Run the runtime suite to surface rev-2 assumptions**

Run: `uv run pytest tests/arena/test_channel_runtime.py -q`
Tests that assumed a pending offer exists after a successful fund (asserting `payment_gs.pending`, or relying on a pending offer to drive a later step) now fail — update each to rev-3 expectations: after a successful fund the pair is absent and the treasuries have moved. Do not change tests that plant offers explicitly via `install_exact_offer`.

- [ ] **Step 3: Write the failing no-transfer test**

```python
@pytest.mark.asyncio
async def test_fund_fails_closed_when_send_reports_success_without_transfer(
    tmp_path, payment_gs
):
    rt, deal = await accepted_payment_deal(
        tmp_path, payment_gs, timing="up_front"
    )
    real_offer = payment_gs.offer_channel_payment

    async def no_transfer(payee, gold):
        result = await real_offer(payee, gold)
        if result == "CHANNEL_PAYMENT_PROPOSED":
            # Undo the fake's enactment: engine said yes, gold never moved.
            payment_gs.treasury[payment_gs.local_player] += gold
            payment_gs.treasury[payee] -= gold
        return result

    payment_gs.offer_channel_payment = no_transfer
    ack = await apply_payment_action(
        rt,
        payment_gs,
        deal.proposer,
        "fund_deal",
        {"deal_id": deal.id},
        turn=3,
    )
    assert ack.status == "rejected"
    assert rt.deal(deal.id).payment_status is PaymentStatus.DUE
    events = journal_events(rt)
    assert events[-1]["kind"] == "payment_fund_result"
    assert events[-1]["payload"]["engine_result"] == (
        "Error: CHANNEL_PAYMENT_NOT_ENACTED"
    )
```

Run: `uv run pytest tests/arena/test_channel_runtime.py -k without_transfer -v` → FAIL (currently commits OFFERED).

- [ ] **Step 4: Implement the verification**

Add the import at the top of channel_runtime.py:

```python
from civ_mcp.arena.channel_terms import ObservationFamily, ObservationRequest
```

Add two helpers near `_payment_state_status`:

```python
    async def _read_payment_treasuries(
        self, gs, deal: Deal, turn: int
    ) -> dict[str, int] | None:
        values: dict[str, int] = {}
        for key, pid in (
            ("payer_gold", deal.proposer),
            ("payee_gold", deal.counterparty),
        ):
            request = ObservationRequest(
                families=frozenset({ObservationFamily.TREASURY})
            )
            try:
                observed = await gs.get_channel_observation(pid, turn, request)
            except Exception:
                return None
            if observed.errors or (
                ObservationFamily.TREASURY not in observed.families_present
            ):
                return None
            values[key] = observed.treasury_gold
        return values

    async def _verify_synchronous_settlement(
        self, gs, deal: Deal, turn: int, baseline: dict[str, int]
    ) -> bool | None:
        """True: enacted (exact delta, pair absent). False: verifiably not
        enacted. None: unreadable — the caller must leave the intent
        unfinished so recovery fail-closes (no delta-based fund recovery)."""
        result = await self._read_payment_treasuries(gs, deal, turn)
        if result is None:
            return None
        try:
            payment_state = await gs.get_channel_payment_state(
                deal.proposer, deal.counterparty, deal.payment_gold
            )
        except Exception:
            return None
        status = self._payment_state_status(payment_state, deal)
        if status is None:
            return None
        gold = deal.payment_gold
        return (
            status == "absent"
            and result["payer_gold"] == baseline["payer_gold"] - gold
            and result["payee_gold"] == baseline["payee_gold"] + gold
        )
```

In `_fund_deal`: after the preflight status check and **before** `_commit_payment_intent_for_action` (a pure read; nothing is committed if it fails):

```python
        baseline = await self._read_payment_treasuries(gs, deal, turn)
        if baseline is None:
            raise _ActionRejected("could not read the settlement baseline")
```

Replace the success commit (`if self._funding_succeeded(engine_result): offered = replace(...); return self._commit_payment_result(...)`) with:

```python
        if self._funding_succeeded(engine_result):
            settled = await self._verify_synchronous_settlement(
                gs, deal, turn, baseline
            )
            if settled is None:
                return ChannelAcknowledgement(
                    staged.actor,
                    turn,
                    staged.source_id,
                    "rejected",
                    "payment funding outcome requires recovery after an "
                    "unreadable settlement check",
                )
            if not settled:
                return self._commit_payment_result(
                    "payment_fund_result",
                    intent,
                    engine_result="Error: CHANNEL_PAYMENT_NOT_ENACTED",
                    recovery=None,
                    deal=None,
                    grievance=None,
                    message=(
                        "payment send reported success but settlement "
                        f"verification shows no enactment for {deal.id}"
                    ),
                )
            offered = replace(
                deal,
                payment_status=PaymentStatus.OFFERED,
                payment_response_by_turn=(
                    turn + self.rules.payment_response_turns
                ),
            )
            return self._commit_payment_result(
                "payment_fund_result",
                intent,
                engine_result=engine_result,
                recovery=None,
                deal=offered,
                grievance=None,
                message=f"funded unofficial deal {deal.id}",
            )
```

(The `None` arm mirrors the existing send-exception path: the intent stays unfinished and the established fail-closed recovery owns the outcome. The `False` arm rides the validator's existing `_authoritative_payment_failure` acceptance — `deal=None` keeps funding due and retryable.)

- [ ] **Step 5: Run the runtime suite, fix any remaining fake fallout**

Run: `uv run pytest tests/arena/test_channel_runtime.py -q` → PASS (including the new test). If any fund test uses a gs fake without `get_channel_observation` (e.g. `FakeGameState`), give it the same `observation(...)`-based method or switch the test to `payment_gs`.

- [ ] **Step 6: Full suite, then commit**

Run: `uv run pytest tests -q` → PASS.

```bash
git add src/civ_mcp/arena/channel_runtime.py tests/arena/test_channel_runtime.py
git commit -m "fix(arena): verify synchronous settlement on the ordinary-run fund path"
```

---

### Task 11: Final verification sweep

**Files:** none (verification only; fix anything it surfaces).

- [ ] **Step 1: Full suite and hygiene checks**

```bash
uv run pytest tests -q
git diff --check
rg -n "RECOVERED_EXACT_CHANNEL_PAYMENT|observed_exact_offer|response_retried|response_retry_query_failed|respond_to_channel_payment|build_channel_payment_response|_response_succeeded" src tests
rg -n '"enacted linked payment was rejected"' src
```

Expected: suite PASS, clean diff check, zero matches on both greps.

- [ ] **Step 2: Replay guard on real artifacts**

Confirm existing run journals still open (they contain no payment intents, so all tasks are compatible — this is the belt-and-braces check):

```bash
uv run python - <<'EOF'
import pathlib
from civ_mcp.arena.channel_runtime import ChannelRuntime
from civ_mcp.arena.config import ChannelRules
for run_dir in pathlib.Path("arena_runs").iterdir():
    journal = run_dir / "channel_journal.jsonl"
    if journal.exists():
        print(run_dir.name, "opens" )
EOF
```

(If the journal filename or `open` signature differs, mirror how `tests/arena/test_channel_runtime.py::runtime` opens a run directory.) Expected: every listed run prints without raising.

- [ ] **Step 3: Verify no stray behavior drift in the gate flow**

Run: `uv run pytest tests/arena/test_live_gate_channels.py tests/arena/test_experiment.py -q`
Expected: PASS; `test_revision_3_failure_codes_registered` and `test_scenario_revision_is_3_for_synchronous_settlement` unchanged and green.

---

## Self-Review

- **Coverage:** all 10 review findings have a task — F1→Task 5, F2→Task 6, F3→Task 10, F4→Task 7, F5→Task 3, F6→Task 4, F7→Task 8, F8→Task 9, F9→Task 1, F10→Task 2.
- **Ordering:** deletions and small driver fixes first (Tasks 1–5), semantic runtime fixes next (Task 6), then the validator cleanup chain in dependency order (7 → 8 → 9 — Task 9 merges branches Task 7 creates), and the largest change (Task 10) last, isolated.
- **Type consistency:** `_settlement_read_pending(player_id) -> bool` (Tasks 2, 5); `_settlement_verdict(...) -> str | None` (Task 4); `_absent_response_records(...) -> tuple[Deal, dict | None, str]` (Task 9); `get_current_game_turn() -> int` on both `GameState` and `GateGameState` (Task 5); `_verify_synchronous_settlement(...) -> bool | None` (Task 10).
- **No revision bump, no new reason codes** — all new granularity is in `detail.failure` strings, satisfying the registered-codes test.
