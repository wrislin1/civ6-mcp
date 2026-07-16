# Arena Unofficial Channels — Private Bilateral LLM↔LLM Deals & Grievances (Design)

**Date:** 2026-07-09 · **Revised:** 2026-07-16 (delta review against post-seat-0 main + seat-0 participation + human web surface; riz-approved section-by-section)
**Status:** ACTIVE — the next slice up. Original design approved by riz 2026-07-09; the 2026-07-16 revision re-verified every code claim, added the CLI captured-line entry path (Section 2), seat-0 participation (Section 8), the human web surface (Section 9), and the analyst view (Section 10).
**Predecessor:** Slice 4 (full toolset + era gating) merged at `b3540d8`; Attention & Turn-Skipping merged at `7f1ac2c` with live probes P1–P4 **PASSED 2026-07-14** (`480fc8d`); Seat-0 Piloting merged at `845ae09` with all three attended live gates **PASSED 2026-07-15/16** under the full-LLM-control directive (spec: `docs/superpowers/specs/2026-07-14-arena-seat0-piloting-design.md`).
**Sequencing:** This is the substance of roadmap item **A (LLM↔LLM interaction)** in the D → A → C → B order. Everything riz inserted ahead of A has shipped; this slice is **queued for implementation** (plan next).
**Scope decision:** Channels **including seat-0 participation** — an LLM-piloted seat 0 participates natively; a human-controlled seat 0 participates through a run-dir queue + LAN web surface (Sections 8–9). The original channels-only carve-out of seat 0 is obsolete; Appendix A is retained as a historical record (superseded).

## Context & Motivation

The arena runs one LLM per civ seat. Today those LLMs never talk to each other: each
puppet turn is an isolated invocation whose prompt is assembled from a briefing +
`memory_block` + `task_block` + a turn announcement (`build_opening_prompt`,
`prompting.py`), driven seat-by-seat by the coordinator (`coordinator.py`); since
2026-07-15, seat 0 is played in place by an autonomous pilot through that same
coordinator path. Diplomacy, if it happens at all, happens only through the game's
official channels (`propose_trade`, `send_diplomatic_action`, the World Congress) —
all of which the game engine mediates and enforces.

The **unofficial channels** add a side-band the game engine knows nothing about: a civ
can send another civ a free-text message and attach an *enforceable* structured deal
("destroy this barbarian camp and I'll pay you 100 gold"). The recipient can honor the
deal or not. Because the favor half of a deal is an action only the recipient can
voluntarily take, the game cannot force it — so a broken promise leaves an **unofficial
grievance**: an arena-tracked, private, bilateral reputation mark that colors future
dealings but never touches the game's own grievance system.

The research payoff is emergent social behavior: trust, reciprocity, betrayal, blackmail,
and — because unofficial grievances are invisible to the engine — wars that read as
**unprovoked** to every onlooker even though the aggressor feels wholly justified.

### Feasibility summary (verified against current code)

- **The plumbing already exists four times over.** Per-civ persisted state injected
  pre-turn and captured post-turn is exactly how `memory.py` (StandingMemory),
  `task_tracker.py` (TaskState), and `attention.py` (AttentionState) work —
  schema-versioned JSON under the run dir, formatted into a prompt block inside
  `run_arena` (block assembly ~`coordinator.py:1066-1130`; post-turn capture at the
  seat-0 ~`:1503` and played ~`:1707` sites — line numbers drift, anchor on symbols)
  and injected via `build_opening_prompt` (`prompting.py`). The unofficial channel is a
  **fifth instance of that same pattern**; no new architecture.
- **No new game-engine coupling.** Payments ride the existing `propose_trade` tool; the
  game guarantees the transfer once accepted. Everything else is arena-side bookkeeping +
  prompt injection. Nothing writes to the game's grievance/diplomacy engine.
- **Verification is offline-testable.** Each structured term reduces to a pure function
  over game-state snapshots, so verifiers are unit-testable without a live game.
- **Distinct from the game's real grievances.** Civ6 has its own grievance system, already
  surfaced read-only via `get_gossip` ("Grievances both directions per met civ plus recent
  gossip", `registry.py:1243`). Unofficial grievances are a *separate* arena ledger and are
  never merged into, or derived from, the game's grievances.

## Decisions (riz, this session)

Locked tenets, in the order they were settled:

1. **Arena-layer only.** Unofficial grievances never touch Civ6's grievance/diplomacy engine.
2. **Real-game verification.** A deal is "honored" only when the arena confirms the favor
   against actual game state — never on the recipient's say-so.
3. **Unprovoked-by-design.** An action driven by an unofficial grievance carries no official
   justification, so to the game and every other civ it reads as unprovoked and eats the
   normal warmonger/reputation cost.
4. **Private & bilateral.** A message or grievance is known only to the two parties. Nothing
   leaks automatically; a civ may *choose* to pass it on, but that is just another message.
   This is the property that makes #3 hold and makes betrayal both risky and deniable.
5. **Payment via real trades.** The gold leg of a deal goes through the game's real trade
   system (self-enforcing once accepted); "outside the grievance system" ≠ "outside the
   trade system."
6. **Free-text messages + structured deal terms.** Communication is unrestricted prose;
   only a *structured* term attached to a message is verified and can generate a grievance.
   Free prose is pure talk — the home of "complex long-term strategy" until model
   decision-making is strong enough to make prose commitments bite.
7. **Per-deal payment timing.** A `timing` flag (`up_front` | `on_delivery`) is set by the
   proposer; grievances can fire on either side depending on who defaults on the unfunded leg.

Three judgment calls, resolved with riz's approval (each may be revisited at plan time):

- **J1 — Explicit acceptance.** A deal is activated by an explicit `respond_to_deal(accept)`,
  not inferred from the payment trade. Clean, testable state machine.
- **J2 — Attribution = condition-met-by-deadline.** For a favor like `destroy_camp`, the
  condition being true by the deadline counts as honored regardless of *who* caused it. Simple
  and exact; the known edge case (a third party clears the camp and the payee is credited for
  nothing) is accepted for v1. Strict "counterparty's unit did it" is deferred (needs
  kill-attribution the game may not cleanly expose).
- **J3 — Slow decay.** A grievance's weight decays over ~N turns so recent betrayals bite and
  old ones fade, rather than persisting undiminished forever.

## Section 1 — Architecture & Data Model

A new module `src/civ_mcp/arena/channels.py`, sibling to `memory.py` and `task_tracker.py`,
owning per-run JSON state (schema-versioned) with three structures. **Every read is
scoped to a single civ**: a civ's injected view contains only rows where it is `from`/`to`,
`proposer`/`counterparty`, or a party to the grievance.

**Messages** — append-only log:
```
{ id, from_player, to_player, turn, text, deal_id? }
```

**Deals** — structured commitments:
```
{ id, proposer, counterparty,
  favor: { term_type, params },        # e.g. term_type="destroy_camp", params={x,y}
  payment: { gold: N },                # rides propose_trade
  timing: "up_front" | "on_delivery",
  deadline_turn,
  state: "proposed"|"declined"|"active"|"honored"|"broken"|"expired",
  created_turn,
  baseline_snapshot }                  # game state captured at creation, for the verifier
```

**Grievances** — per ordered pair `(wronged → offender)`:
```
{ id, wronged, offender, reason, deal_id, turn, magnitude, decay_ref }
```

Persistence mirrors the existing modules: `load_channels`/`save_channels`, a
`format_channel_block(player_id, ...)` that renders the civ's private inbox + active deals +
standing grievances into a prompt block, and a `SCHEMA_VERSION` constant.

## Section 2 — Entry Paths: Tools (API civs) & Captured Lines (CLI civs)

*(Revised 2026-07-16. The original tools-only design reached API-driven `LLMPolicy` civs
but not CLI civs, whose tool surface is the real civ6 MCP server — `.mcp.json`,
deny-lists, env-gated server-side stripping — not the arena registry. Both entry paths
below converge on the same `channels.py` apply functions. API dispatch is in-process and
sequential with the coordinator, and CLI lines are applied post-turn by the coordinator,
so a single-writer discipline holds everywhere with zero locking.)*

**API-driven civs — registry tools**, gated per-civ. Each is a normal tool call and
therefore consumes one step of the civ's `max_steps` turn budget.

- **`send_message(to_player, text, deal=None)`** — free prose, plus an optional structured
  `deal` term (`favor`, `payment`, `timing`, `deadline_turn`). Creates a Message row and,
  if `deal` is present, a Deal row in state `proposed`.
- **`respond_to_deal(deal_id, accept|decline)`** — the recipient's handshake (J1). `accept`
  moves the deal to `active` and starts the obligation clock; `decline` closes it.
- **Gating note (2026-07-16):** the `full` tier is `tuple(TOOL_REGISTRY)`, so channel
  tools must be gated by the `channels` knob composed with the tier — never by tier
  membership alone.

**CLI civs (cli-claude / codex, including a CLI seat-0 pilot) — captured lines** in the
final summary, applied post-turn by the coordinator: the exact `TASK:` / `SKIP:`
precedent, riding the raw-summary path the attention slice pinned (the clamp-survival
battle is pre-won). Three forms (exact grammar is plan-time):

- `MSG to=<pid>: <text>`
- `DEAL to=<pid> favor=<term>(<params>) pay=<gold> timing=<up_front|on_delivery> deadline=<turn>: <text>`
- `DEAL ACCEPT <deal_id>` / `DEAL DECLINE <deal_id>`

Malformed lines are dropped fail-open (a bad line never aborts capture) and echoed back in
the civ's next channel block ("your DEAL line failed to parse: …") so the model can
self-correct. CLI civs get no mid-turn validation feedback — accepted as the cost of zero
new MCP-server surface.

The recipient's **inbox is auto-injected** into its opening prompt as `channel_block`,
slotted between `task_block` and `digest_block` in `build_opening_prompt`'s fixed ordering
(`prompting.py`), so no explicit read tool is needed. New messages, active deals awaiting a
response, deals the civ owes on, and standing grievances all render in that block.

## Section 3 — Payment & the Real Trade System

The gold leg is executed through the existing `propose_trade` flow. The game guarantees the
transfer once both sides accept, so **payment itself is never verified by the arena** — the
engine already did. The risk lives entirely in the *unfunded* leg, which the `timing` flag
selects:

- **`up_front`** — payer pays now (real trade); the favor is still owed. If the favor is not
  verified by the deadline → grievance on the **counterparty**. (Payer bears the risk — the
  "I paid you and you did nothing" case.)
- **`on_delivery`** — the favor is verified first; the payment is still owed. If the payer
  does not complete the gold trade by the deadline → grievance on the **proposer**. (Payee
  bears the risk.)

The arena links a deal to its payment by observing the trade (exact observation mechanism —
trade-log read vs. gold-delta — is an implementation detail for the plan, not a design fork).
*(2026-07-16: since the seat-0 hardening wave, `get_pending_trades`/`respond_to_trade` live
in **every** tier, so the payment leg is answerable even by minimal-tier civs.)*

## Section 4 — Lifecycle & Verification

State machine:
```
proposed ──accept──▶ active ──▶ honored | broken
    │
    ├──decline──▶ declined
    └──deadline reached before accept──▶ expired
```
(`expired` is the never-accepted path only; once `active`, the terminal states are
`honored` or `broken` — no overlap between the two.)

- The proposer sets a **bounded** `deadline_turn`.
- At creation the arena captures `baseline_snapshot` for the favor term (e.g. "camp exists at
  (x,y)"; relevant gold balances).
- At/after the deadline (and optionally each turn) the term's **verifier** — a pure function
  over the baseline + live game state — rules the favor satisfied or not. **Attribution = J2**
  (condition-met-by-deadline).
- Verifier outcomes drive the terminal state and, on default, write a grievance.

**Starter term catalog (v1):** `pay_gold(N)`, `destroy_camp(x,y)`,
`dont_settle_within(r,x,y)`, `declare_war_on(civ)`, `keep_peace_with(civ, until_turn)`,
`spread_religion_to(city)`. Each ships with its own verifier + tests. The catalog is designed
to grow; free-text messages carry everything not yet in it.

## Section 5 — Grievance Model

- On a broken deal the arena writes a grievance with **`magnitude`** = the stiffed value
  (the gold amount, or a fixed unit per favor).
- **Decay (J3):** magnitude decays slowly over ~N turns; the effective weight surfaced to the
  model is the decayed value.
- **Surfacing — private, both directions.** The grievance renders in *both* parties' channel
  blocks: the wronged civ sees, e.g., "Rome took 100g on turn 42 and never destroyed the camp
  at (12,7)"; the offender sees "You owe Egypt a paid-for camp-kill; Egypt distrusts you." This
  is the entire behavioral lever — it drives retaliation, refusal of future deals, and the
  unprovoked-looking war. Nothing about a grievance is ever shown to a third party.
  *(The read-only `/analyst` spectator route — Section 10 — is the sole out-of-band
  exception, for the human experimenter, never for a civ.)*

## Section 6 — Agency, Config & Cost

- Channel tools sit behind a per-civ **`channels` knob in the options fingerprint**
  (off/on), exactly like `memory` / `task_tracker` / `briefing`. Off by default; opt-in per
  experiment. *(2026-07-16: shape it as a nested options object with `enabled` + parameters
  — decay `N`, per-pair caps — mirroring `attention`/`memory` in `CivOptions.fingerprint()`.
  Seat 0 is a normal `players:` entry, so the knob applies to it unchanged; no
  seat-0-specific validation is needed — `validate_arena_config`'s attention-off rule for
  seat 0 is unrelated.)*
- The playbook (`playbook.md`) gains a short section nudging civs to consider unofficial
  diplomacy when it serves their victory path.
- Using the channel spends turn steps → real token cost (and, for `cli-claude`, real API
  spend). This is the reason it is opt-in and fingerprinted, so A/B arms can isolate its
  effect.

## Section 7 — Testing

- **Verifiers:** pure-function unit tests per term type over synthetic before/after
  game-state snapshots (offline; the pattern that makes `task_tracker` verifiers testable).
- **Lifecycle:** state-machine tests (propose → accept/decline → honored/broken/expired) with
  a fake clock and fake game state.
- **Privacy invariant:** a property test asserting `format_channel_block(p)` never contains a
  row `p` is not a party to — the single most important correctness guarantee (tenet #4).
- **Grievance decay:** deterministic decay-curve tests.
- **Coordinator wiring:** the block is injected pre-turn and channel state captured post-turn,
  mirroring the existing memory/task capture tests.
- **Fingerprint:** the `channels` knob appears in `CivOptions.fingerprint()`.
- **Live gate (deferred to plan):** one live run with two channel-enabled seats exchanging a
  real deal, since the `propose_trade` linkage and game-state verifiers can only be fully
  trusted against the real game.

Additions (2026-07-16):

- **Captured-line parser:** grammar accept/reject tests, malformed-line fail-open + echo,
  clamp survival on the raw-summary path.
- **Queue application:** ordering, malformed entries, ack round-trip into the rendered view.
- **Web backend:** scoped rendering, SSE snapshot shape, POST validation, token check —
  all offline via a test client.
- **Privacy extension:** the privacy property above extends to the human surfaces —
  default-route output and the seat-0 view file must be subsets of seat-0's scoped rows
  (Section 10).
- **Live gate addition:** one human send/respond round-trip through the web page during an
  attended run.

## Section 8 — Seat-0 Participation (added 2026-07-16)

**LLM-piloted seat 0: free by construction.** Puppet turns and seat-0 admissions share one
coordinator path — the same code builds `memory_block`/`task_block` (and now
`channel_block`) for whichever seat is played. Seat 0 is a normal `players:` entry, so the
`channels` knob on its `CivOptions` just works: a CLI pilot uses captured lines, an API
pilot uses the registry tools. Deal verifiers are player-agnostic; deals with seat 0
verify identically.

**Human-controlled seat 0** (no pilot configured, or a turn left in human-pending): the
coordinator still owns all channel state — deadlines, verification, and grievances tick
regardless of who plays seat 0.

- Seat-0's **scoped** view renders to `channels/seat0_view.md` under the run dir on every
  state change, plus a console notice line.
- Human actions (send / accept / decline) enter through an append-only
  `channels/seat0_queue.jsonl`; the coordinator applies queued actions at its next poll and
  acknowledges each applied action back into the view file.
- "Next poll" latency is consistent with the async message-passing model (Non-Goals) — no
  special timing semantics for humans.

## Section 9 — Human Web Surface (added 2026-07-16)

A new small module (`channels_web.py`) run as a **separate process** — spawnable via a
watcher CLI flag (`--channels-web[=PORT]`) or standalone — whose entire contract with the
arena is the two Section-8 files: it reads channel state to render, and appends to the
queue to act. It can never race the coordinator or touch live turn piloting (a wedged
request handler is invisible to the run).

- One self-contained page, inline HTML/JS — deliberately **not** part of the `web/` bun
  toolchain: that app is the Convex-backed public showcase, and live private control
  traffic stays off it and off the cloud.
- `GET /` (page), `GET /events` (SSE; server-side ~1s mtime poll of the state file —
  channel traffic is turn-paced, so this is genuinely realtime), `POST /send`,
  `POST /respond` (validate → append to queue).
- Binds LAN-visible with an optional bearer-token env var. A `brothereye.net` hostname via
  Caddy (plus Cloudflare Access if desired) is a one-line mapping in the brothereye repo,
  not here.
- The queue-file format is the contract, so a trivial CLI send/respond fallback stays
  nearly free (plan decides whether it ships in v1).

## Section 10 — Analyst View & Privacy Extension (added 2026-07-16)

The default page serves **only seat-0's scoped view** — tenet #4 extends to the
human-as-player. A separate read-only **`/analyst`** route exposes the full ledger (all
messages, deals, grievances) for *spectating* LLM-piloted runs, with a documented caveat:
opening it while playing seat 0 makes you an omniscient player and contaminates the run.
The privacy property test covers both: default-route output and the view file must be
subsets of seat-0's scoped rows; only `/analyst` may exceed them.

## Non-Goals

- **No writes to the game's grievance/diplomacy engine** (tenet #1). Unofficial grievances
  stay arena-side.
- **No third-party propagation / gossip of unofficial deals or grievances** (tenet #4).
- **No shadow economy.** Gold moves only through real trades; the arena does not track a
  parallel currency.
- **No free-text adjudication in v1.** Only structured terms are verifiable; prose is talk.
- ~~**No autonomous seat 0**~~ — *obsolete 2026-07-16*: seat 0 is autonomous (merged
  `845ae09`) and a first-class channel participant (Section 8).
- **No synchronous, same-round negotiation.** Turns are sequential; v1 is asynchronous
  message-passing (a reply lands when the recipient next plays; human seat-0 actions land
  on the coordinator's next poll). Real-time bargaining remains a later concern — now a
  pure simplicity choice, no longer entangled with seat-0 work (shipped).

## Open Items (for plan time)

- Bounds: max `deadline_turn` horizon; max active deals per pair; inbox/message-log pruning
  policy (cap + newest-first, like the gossip readout).
- Exact payment-observation mechanism for the `propose_trade` linkage (trade-log vs. delta).
- `magnitude` units (raw gold vs. normalized) and the decay half-life `N`.
- Whether declined/expired deals leave any trace (a soft "they wouldn't deal" signal) or vanish.
- Playbook wording and whether channel use should be nudged or purely emergent for a cleaner
  research signal.
- *(2026-07-16)* Exact captured-line grammar (params encoding, deal-id format) and where the
  parser lives.
- *(2026-07-16)* `seat0_queue.jsonl` entry schema + ack format.
- *(2026-07-16)* Web defaults: bind address/port, token env name, SSE poll interval; whether
  the CLI fallback ships in v1.

---

## Appendix A — Carried-forward findings: Autonomous seat 0 (separate future spec)

> **SUPERSEDED 2026-07-16.** The seat-0 piloting slice shipped: merged `845ae09`, all three
> attended live gates passed 2026-07-15/16 (spec
> `docs/superpowers/specs/2026-07-14-arena-seat0-piloting-design.md`). `hook.py` now has an
> `end_turn` builder; the coordinator owns turn-end with a blocker sweep, phase machine, and
> human-pending fallback; the restore-to-self loop is fixed (seat 0 is played in place).
> Everything below is a historical record of the pre-seat-0 world.

riz wants LLM-controlled seat 0 (fully autonomous, no human in the loop). That is a **separate
brainstorm + live-gate plan**, not part of this slice, because it is live-Lua turn-mechanics
work with the opposite risk profile from the channels feature (which is offline-testable
Python). These findings are recorded here so they are not re-discovered from scratch:

- **Verified current state:** every puppet turn ends with `finish_units(K)` + `restore_local(0)`
  and **no** explicit end-turn (`coordinator.py:445-446`). A DESIGN NOTE directly above
  (`:439-444`) already spells out the fix. `hook.py` has no end-turn builder yet
  (`build_inject` / `build_finish_units` / `build_restore_local` only).
- **Two coupled fixes required:** (1) issue a real `UI.RequestAction(ACTION_ENDTURN)` for the
  local seat so it advances without a human click; (2) fix the seat-0 restore-to-self loop —
  `restore_local(0)` hands control back to the seat that just played, so an all-puppet game
  replays seat 0 forever (proven in the 8-civ smoke: seat 0 replayed 12/16 turns, seats 5-7
  never moved). Fix = restore to a non-active observer, or gate on turn-advance.
- **Hazard:** validation is only possible live against the running game, and stopping a watcher
  mid-AI-phase has hung the game and cost a save-reload. Requires the human-in-loop safety
  invariant (`Players[0]:IsTurnActive()==true` before any stop).
- **Relationship to channels:** channels do **not** depend on autonomy — seats 1..N can already
  message each other in the human-in-loop model. Autonomy turns channels from a human-advanced
  feature into a self-running all-LLM showcase.
- See memory `reference-arena-no-autonomous-mode` for the full operational detail.
