# Unofficial Channels Core — Attended Live Gate

> **Status 2026-07-22: lifecycle PASS** on attended run
> `arena-channels-core-gate-v4` (scenario revision 3, main at `453a902`).
> Every pass-contract row that concerns the channel lifecycle now has direct
> live evidence — see the "2026-07-22 attended rerun" section at the end of
> this document. The lifecycle PASS is distinct from the raw FireTuner-probe
> ledger below, which remains its own record: the formal probe table was
> superseded in part by the gate-v1/v2/v3 investigation probes and by rev-3's
> in-run settlement evidence, but it was never re-executed as written.
> The verdict paragraph and evidence sections that follow describe the
> original 2026-07-16 attempt and are retained unchanged as history.

**Verdict (2026-07-16 attempt): FAIL / BLOCKED.** The attended run exercised one API puppet turn and
one CLI puppet turn, but neither agent emitted a channel action. Consequently it
did not create the required canary, messages, deals, payment, restart
reconciliation, terminal outcomes, or grievance. The inherited raw FireTuner
probes also remain unexecuted. This is evidence that the gate did not pass, not
evidence that those behaviors failed.

No Lua was changed in response to this attempt. Human control was returned at
turn 315 with the hook inactive, and the final ownership check found no watcher,
Codex, MCP, or connected FireTuner client left by the gate.

## Pass contract

One attended run must provide all of the following before this gate can pass:

- persisted acknowledgements from both the API and CLI paths;
- an up-front deal that becomes `honored`;
- an on-delivery deal that becomes `broken` from deterministic evidence;
- one continuous unit or trade-route term through its inclusive deadline;
- a deliberate process restart while an exact official payment is pending;
- matching official pending-trade fingerprints before and after restart;
- a player-1/player-2 canary absent from every player-3 projection, prompt,
  acknowledgement, and transcript inspected;
- raw live answers for each inherited engine probe below; and
- analyzer output containing at least one `honored`, one `broken`, one
  `settled` payment, and one deterministic grievance.

If a live FireTuner response differs from an offline builder/parser assumption,
the operator must stop and preserve the raw response before changing Lua.

## Run record

| Field | Recorded value |
|---|---|
| UTC start | `2026-07-16T22:35Z` |
| run ID | `arena-channels-core-smoke` |
| loaded save | `TASK12_CHANNELS_GATE_T314_20260716T2240Z.Civ6Save`, turn 314, local player 0; Windows file 5,981,644 bytes |
| branch | `arena-unofficial-channels-core` in an isolated worktree |
| tested code | base `640efc60ffd2ac8492babec2f117ec79e19d11b7` plus hash-verified uncommitted Task 12 files |
| remote worktree | `riz@192.168.20.141:~/projects/civ6-mcp-task12-live`; remote `main` untouched |
| API seat | player 1, `local` / `gemma4-26b`, `http://192.168.20.196:11440/v1` |
| CLI seat | player 2, `cli-codex` / `gpt-5.5` after the recorded provider substitution |
| `.mcp.json` | isolated worktree, SHA-256 `42baa0b2293bc470b4919efdf6f501b23d6a74d2dc460a0c4d43dc5b2956582b` |
| run directory | `arena_runs/arena-channels-core-smoke` in the remote isolated worktree |
| player-1 result | `.arena-runs/arena-channels-core-smoke.cycle1.out` |
| player-2 result | `.arena-runs/arena-channels-core-smoke.out` |
| channel state/journal | `channels/state.json`, `channels/events.jsonl` |

### Provider preflight and substitution

The plan's original `gpt-5` CLI identity was tested before any watcher or direct
hook owner was started. The authenticated provider rejected it:

```json
{"type":"error","message":"{\"type\":\"error\",\"status\":400,\"error\":{\"type\":\"invalid_request_error\",\"message\":\"The 'gpt-5' model is not supported when using Codex with a ChatGPT account.\"}}"}
{"type":"turn.failed","error":{"message":"{\"type\":\"error\",\"status\":400,\"error\":{\"type\":\"invalid_request_error\",\"message\":\"The 'gpt-5' model is not supported when using Codex with a ChatGPT account.\"}}"}}
```

Work stopped at that point. Riz authorized only the provider-model substitution
to the live skill's known-good `gpt-5.5`. The repeated non-game probe returned:

```json
{"type":"item.completed","item":{"type":"agent_message","text":"TASK12_CODEX_AUTH_OK"}}
{"type":"turn.completed","usage":{"input_tokens":18799,"cached_input_tokens":10112,"output_tokens":10,"reasoning_output_tokens":0}}
```

The GPU-0 endpoint advertised the exact model id `gemma4-26b`. The local model,
gateway, channel rules, save, and Lua assumptions were not substituted.

## Ownership, crash, and attempt chronology

FireTuner ownership was checked before every direct connection and watcher arm.
The initial maps showed only the Windows `127.0.0.1:4318` listener, with no
`ESTABLISHED`, `CLOSE_WAIT`, or `FIN-WAIT` client.

1. The first arm waited through the default 600 one-second idle polls without a
   human turn transition and exited with `puppet_turns_played: 0`. Its PID/PGID
   was `31509/31509`, child `31513`. Cleanup showed turn 314 and an inactive
   hook.
2. A later arm was interrupted when Civilization VI crashed. Riz restarted the
   game. The empty partial artifacts were preserved instead of overwritten at
   `.arena-runs/crash-20260717T0000Z/` and
   `arena_runs/arena-channels-core-smoke-crash-20260717T0000Z/`.
3. After a new ownership check, the watcher was rearmed and Riz ended the human
   turn. Player 1 was admitted at turn 314. That process exited after one puppet
   because the YAML had omitted `max_puppet_turns` and inherited its default of
   1.
4. Player 2 was then active, so a continuation watcher reopened the same
   run/journal. Its watcher PGID was `2421`, child `2429`, with Codex PID `2434`.
   It admitted player 2 at turn 314 and also exited after one puppet.
5. Cleanup restored the human at turn 315. The final state was
   `PuppetState(local=0, turn=315, active=False, last=0, seat0_active=True)`.
   The final owner map showed no watcher, Codex, MCP, or connected 4318 socket.

The checked-in reproducibility config now sets `max_puppet_turns: 20`, matching
the `max_game_turns: 20` captured-turn budget so a future invocation can remain
armed for the entire planned lifecycle instead of exiting after the first
configured seat cycle. This post-run correction was not exercised live and is
not evidence of a passing gate.

## Puppet-turn evidence

| Seat | Result |
|---|---|
| player 1 / API | one turn at 314; `max_steps reached`; 47,164 prompt tokens, 1,460 completion tokens; channels enabled, acknowledgements 0 |
| player 2 / CLI | one turn at 314; exit 0; 714,212 prompt tokens, 4,179 completion tokens; channels enabled, acknowledgements 0 |

Player 2's recorded summary was:

> Turn 314 actions done: set Texcoco to a 4-turn Builder, switched civic to
> 1-turn Military Training, built routes with all idle Military Engineers,
> activated Sarah Breedlove, and moved the Tank plus Great General toward the
> northwest barbarian threat; I did not end the turn.

Neither transcript staged a `CHANNEL {...}` action. The canonical channel
snapshot therefore contains no messages, deals, grievances,
acknowledgements, applied source ids, or applied request ids.

### Empty observation explanation

The journal contains four `observation_recorded` events: admission and finish
for each player. All four contain `families_present: []`, empty family arrays,
`treasury_gold: 0`, and `errors: []`.

This shape is expected for these turns and is **not** a FireTuner API mismatch.
`ChannelRuntime.admit_player()` requests only the favor families needed by
active deals involving that player; no deal existed. `finish_player()` adds
families needed by staged channel actions; neither agent staged one. Both calls
therefore compiled an empty `ObservationRequest`. The run never queried a
non-empty channel observation, so it provides no live validation of the
individual family builders or parsers.

## Inherited raw FireTuner probes

The gate did not create the disposable official trade or safe ownership-change
setup required by these probes. They remain blocked rather than inferred from
offline fixtures.

| Probe | Result | Evidence |
|---|---|---|
| `HasPendingDeal` argument direction | BLOCKED | no official pending deal was created |
| payer/outgoing `GetWorkingDeal` | BLOCKED | no official pending deal was created |
| payee/incoming `GetWorkingDeal` | BLOCKED | no official pending deal was created |
| exact official fingerprint | BLOCKED | no official pending deal was created |
| proposal `SendWorkingDeal` return/effect | BLOCKED | proposal path was not invoked |
| response `SendWorkingDeal` return/effect | BLOCKED | response path was not invoked |
| `CanReceiveInfluence` | BLOCKED | raw protected calls were not executed |
| component id across conquest | BLOCKED | save lacked an approved attended transfer setup |
| component id across loyalty transfer | BLOCKED | save lacked an approved attended transfer setup |

No raw response differed from the offline fixtures because these probes were
not run. A future attempt must preserve unparsed Lua output and stop on the
first disagreement.

## Lifecycle and privacy results

| Required item | Result | Evidence |
|---|---|---|
| API acknowledgement | FAIL | player-1 result records 0 acknowledgements |
| CLI acknowledgement | FAIL | player-2 result records 0 acknowledgements |
| player-3 privacy canary | BLOCKED | no canary was emitted and player 3 was not projected |
| up-front proposal/accept | FAIL | canonical deals list empty |
| continuous unit/trade term | FAIL | canonical deals list empty |
| exact pending offer before restart | BLOCKED | no payment was offered |
| restart/reconcile exactly once | BLOCKED | crash recovery occurred before a payment existed |
| payment settled | FAIL | no payment record exists |
| up-front deal honored | FAIL | terminal outcomes empty |
| on-delivery deal broken | FAIL | terminal outcomes empty |
| deterministic grievance | FAIL | canonical grievances list empty |
| no official grievance mutation | BLOCKED | no breach was adjudicated |

Absence of a privacy leak in a run that never created the canary is not a
privacy PASS.

## Analyzer result

The persisted remote run was copied read-only and analyzed with the Task 12
positional command:

```bash
uv run civ-arena-analyze /tmp/task12-live-artifacts/arena-channels-core-smoke
```

It completed and added the schema-1 channels report. The required assertions
all failed because the run contained no channel actions:

```json
{
  "payments": {"not_due": 0, "due": 0, "offered": 0, "settled": 0, "failed": 0, "waived": 0},
  "outcomes": {"honored": 0, "broken": 0, "declined": 0, "expired": 0, "unverifiable": 0},
  "grievances": {"count": 0, "raw_magnitude": 0.0, "effective_magnitude": 0.0},
  "adjudication_sources": {},
  "pairs": {}
}
```

| Assertion | Result |
|---|---|
| honored >= 1 | FAIL: 0 |
| broken >= 1 | FAIL: 0 |
| settled >= 1 | FAIL: 0 |
| deterministic grievance >= 1 | FAIL: 0 |
| grievance decay math | BLOCKED: no grievance |
| pair/player totals | PASS for the empty canonical state |
| canary isolation | BLOCKED: no canary |

## Artifact hashes

All paths below are relative to the remote isolated worktree unless marked as
the read-only analysis copy.

| Artifact | SHA-256 |
|---|---|
| `arena_runs/arena-channels-core-smoke/channels/state.json` | `137cd11e2e8742ca5a0785d4450b780a16964d551791953ee66a3eaf2a448391` |
| `arena_runs/arena-channels-core-smoke/channels/events.jsonl` | `f72df2ff16de86f914c1ae11870ef4a9bf02803e177f7b2c9d2ed9a056da3f2f` |
| `arena_runs/arena-channels-core-smoke/transcript.jsonl` | `81c358b5f851360c8893dba7d9d363efb0a6eae0e53b021f845476c8be0b3edf` |
| `arena_runs/arena-channels-core-smoke/arena_cost.jsonl` | `bc5d2f642f55ae14a32e09df2423c03eaf3a69f86f7bcdc619110f1b447d2f6a` |
| `.arena-runs/arena-channels-core-smoke.cycle1.out` | `80621fec7cd73ab7f6961b20cf0c3476349c50affdfd6244c9522b9851aecf02` |
| `.arena-runs/arena-channels-core-smoke.out` | `532836836b717e7d3812a917552669f6a9938127f859dbc8bc745a9588341af8` |
| copied `report.json` | `a3e25e0c53a1d191616b1be4ba952a25c04ca6bee4ad67719681a48043f42786` |
| copied `report.md` | `3a06a5ecd8b27379db61702cf7d1020d2e0ccdb6a59c10daa5baaf407c925815` |

Both watcher stderr files were empty (SHA-256
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`).

## Cleanup evidence and next gate

| Cleanup check | Result |
|---|---|
| watcher and cost tails read | PASS |
| final puppet state | PASS: turn 315, local 0, inactive, seat 0 active |
| watcher/process groups exited | PASS |
| final owner map | PASS: no gate-created process or connected socket |
| human control restored | PASS |
| armed processes left | PASS: none |

The next attended run must start from the reviewed Task 12 tip, re-run the full
ownership and provider preflight, deliberately stage both channel actions, and
execute every blocked probe. This report must remain FAIL / BLOCKED until all
pass-contract rows have direct evidence.

---

## 2026-07-22 attended rerun — `arena-channels-core-gate-v4` (revision 3): lifecycle PASS

Intermediate attended runs `arena-channels-core-gate-v1` and `-v2` failed
terminally at the restart checkpoint and proved the engine auto-resolves deals
offered to an AI player (spec revision 2); `-v3` failed with an obsolete
auto-resolution terminal after its probes proved AI→AI `PROPOSED` enactment is
synchronous at send (spec revision 3, Locked Decision 14). Revision 3 —
fund-window settlement verification, ledger-only payment response — plus all
ten findings of the rev-3 code review were implemented and pushed as
`e7e0cb7..453a902` before this run.

### Run record

| Field | Recorded value |
|---|---|
| Date | 2026-07-22, ~17:13–17:35 UTC |
| run ID | `arena-channels-core-gate-v4` |
| tested code | `main` at `453a902`, clean tracked tree, 1963 tests passing |
| loaded save | `CHANNELS_GATE_V1_T157` (manual menu load); verified live: turn 157, local player 0 Korea/Seondeok; players 1 Khmer, 2 Brazil, 3 Cree alive |
| config | `experiments/arena-channels-core-smoke.yaml` (`run_id: arena-channels-core-gate-v4`, `max_puppet_turns: 36`, idle poll limit 1800) |
| roles | `api_actor=1`, `cli_actor=2`, `privacy_observer=3` |
| model/CLI processes | none — deterministic gate mode; cost log `total_usd: 0.0`, `by_player: {}` |
| run directory | `arena_runs/arena-channels-core-gate-v4` (local checkout on the gaming PC) |
| watcher stdout/stderr | `.arena-runs/arena-channels-core-gate-v4.out` / `.err` (stderr empty) |

### Ownership and chronology

The FireTuner slot was owned by a stale session `civ-mcp` (started before the
rev-3 merge); it was used read-only to verify the loaded save, then stopped,
and the free `4318` slot was confirmed before each arm. Each watcher held the
only established `4318` socket for its lifetime.

1. **Invocation 1** (turns 157–158, 6 puppet turns): phases
   `preflight → canary_and_upfront_proposal → accept_upfront → fund_upfront →
   accept_upfront_payment → restart_required`. The funding seat window
   recorded write-ahead treasury baselines, sent the official 1-gold offer
   (payer 1 → payee 2), verified the payment state `absent` and exact
   same-window deltas, and recorded settlement digest `89d8100c487efca6`
   (`payment_checkpoint.json`). Exit at the persisted restart checkpoint with
   `LIVE_GATE … "status":"restart_required"`, `restart_count: 1`.
2. **Restart gap**: the human turn was not ended; the second watcher was
   rearmed with the identical command and run ID ~5 minutes later.
3. **Invocation 2** (turns 158–164, 18 puppet turns): resume verification
   passed — matching gate/channel identities, exactly one prior restart
   request, unchanged settlement digest, payment state `absent`, no duplicate
   acknowledgement — then `restart_verified → await_upfront_favor_deadline →
   verify_upfront_honored → propose_on_delivery → accept_on_delivery →
   await_on_delivery_favor → withhold_on_delivery_funding →
   verify_funding_breach → verify_terminal_gate`, ending with a `gate_passed`
   event and `LIVE_GATE … "status":"passed"`.

`result.json`: `{"status":"passed", "scenario":"unofficial_channels_core_v1",
"scenario_revision":3, "restart_count":1, "phase":"verify_terminal_gate"}`.

### Lifecycle evidence

Gate journal totals: 24/24 budgeted seat captures, 12 phase advances, 7
planned+verified actions, 56 privacy assertions (each checking 6 forbidden
canary digests across 7 capture artifact kinds), 1 `restart_required`, 1
`restart_verified`, 1 `gate_passed` with evidence
`{honored_deal: deal-000001, broken_deal: deal-000002, grievances: 1,
privacy_assertions: 56}`.

Canonical channel state (`channels/state.json`):

| Required item | Result | Evidence |
|---|---|---|
| API + CLI acknowledgements | PASS | 7 acknowledgements, all `applied`, from both actors' production paths |
| player-3 privacy canary | PASS | canary `message_sent` recorded; 56 per-capture assertions found no forbidden digest in any observer artifact |
| up-front deal honored | PASS | `deal-000001`: `payment_status: settled`, `favor_status: satisfied` |
| synchronous settlement | PASS | digest `89d8100c487efca6`; verified `absent` + exact −1/+1 same-window deltas at send; digest unchanged at resume |
| restart/reconcile exactly once | PASS | `restart_count: 1`; `restart_verified` at turn 158 |
| on-delivery deal broken | PASS | `deal-000002`: funding deliberately withheld; `payment_status: failed`; `deal_broken` event |
| deterministic grievance | PASS | `grv-000001`: offender 2, wronged 1, magnitude 0.25, turn 164, "promised payment was not funded by the deadline", `adjudication_source: deterministic` |

### Analyzer result

`uv run civ-arena-analyze arena_runs/arena-channels-core-gate-v4` (schema-1
channels report):

| Assertion | Result |
|---|---|
| honored >= 1 | PASS: 1 |
| broken >= 1 | PASS: 1 |
| settled >= 1 | PASS: 1 |
| deterministic grievance >= 1 | PASS: 1 (raw and effective magnitude 0.25) |
| pair totals | PASS: `1->2` settled+honored; `2->1` failed+broken+grievance |
| observer isolation | PASS: player 3 has 0 messages, 0 deals, 0 grievances |

### Artifact hashes (SHA-256)

Paths relative to `arena_runs/arena-channels-core-gate-v4/` unless noted.

| Artifact | SHA-256 |
|---|---|
| `channels/state.json` | `4f356427f350a412435dafc57161266f7a86bd60dbec882c21dcdba4a36993ac` |
| `channels/events.jsonl` | `107b835ed6f657a3b5892b24f738e6ff2394321456e9c89c383568f1c862d139` |
| `live_gate/events.jsonl` | `f50b411cf1f6cc729f698621ef03726ad5f7fd923cb7e9b08fed374cb1d33aac` |
| `live_gate/state.json` | `7627d357c0c01daa0c90878f20cd64184766ed73a58655889e73dd139af1f19b` |
| `live_gate/result.json` | `64907d5cddf3167e3f4f44b46bf88d35b239c60c83d64ddff045e54892ceee88` |
| `live_gate/payment_checkpoint.json` | `7e4520ddf8def37e8b6064242e623a34e60a968a22a30922a4d6ca4685c54c4f` |
| `transcript.jsonl` | `db2e63dca631cc648f05503620ffb542c9629144eb5171c8bac101fa0ccf0f21` |
| `report.json` | `f21772cee198d28686ba385f68eda01d6fdb77127e83e87d0ed2e6906e6b924c` |
| `report.md` | `7b03ed8e518698b33c0676f36693dbf3270c863ba85aedb62ed0643bc5b950b2` |
| `.arena-runs/arena-channels-core-gate-v4.out` | `7ac52f537230bc0b3eac5420f93a17375694cd5aa6b66420e0fb5de72a4837d7` |
| `.arena-runs/arena-channels-core-gate-v4.err` (empty) | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |

### Cleanup

| Cleanup check | Result |
|---|---|
| terminal result and journal read | PASS |
| watcher/process groups exited | PASS: no `civ-arena`, `codex`, or `civ-mcp` process |
| final owner map | PASS: no established `4318` socket |
| human control restored | PASS: watcher exit 0 after coordinator handback |

### Scope of this PASS

This section records the **lifecycle** PASS: acknowledgements, canary privacy,
deal lifecycle (honored/broken), synchronous settlement with a verified
restart, deterministic grievance, and analyzer assertions. The raw
FireTuner-probe table earlier in this document is a separate ledger; the
gate-v1/v2/v3 investigations answered several of those questions live
(auto-resolution behavior, synchronous AI→AI enactment, working-deal
fingerprints), but the table as written was not re-executed and its rows
remain individually tracked.
