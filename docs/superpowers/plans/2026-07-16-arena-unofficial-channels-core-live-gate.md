# Unofficial Channels Core — Attended Live Gate

**Verdict: FAIL / BLOCKED.** The attended run exercised one API puppet turn and
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

The checked-in reproducibility config now sets `max_puppet_turns: 2`, so a
future invocation can cover both configured puppet seats before exiting. This
post-run correction was not exercised live and is not evidence of a passing
gate.

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
