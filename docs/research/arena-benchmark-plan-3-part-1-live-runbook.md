# Plan 3 Part 1 — live runbook

Operator command order for the live phase. Binding authority stays with the spec
(`docs/superpowers/specs/2026-10-08-arena-benchmark-plan-3-part-1-design.md`) and plan;
this page only sequences the commands. `--help` on every subcommand states its
precondition and outputs in one sentence.

## Before anything

- **cwd = repository root** (the WSL checkout). Every path below is repo-relative.
- **Windows checkout current.** The bridge runs the Windows checkout's code
  (`/mnt/c/Users/wrisl/dev/civ6-mcp`, `game_launcher.WSL_WINDOWS_REPO`; a
  `CIV6_WINDOWS_BOOTSTRAP` override moves both the code and the working directory to the
  checkout that holds the bootstrap) *with that checkout as its working directory*:
  repo-relative archive paths resolve there, so the Windows checkout must be on this
  branch's HEAD and hold the same `benchmarks/saves/`. Nothing is ever written to a
  tracked path on the Windows side: native exports land in its gitignored
  `benchmark_runs/plan3-part1/{bases,exports}/` and are copied into this repo's
  `benchmarks/saves/` from there, so that checkout can always fast-forward to the commit
  that adds an archive. An absolute non-`/mnt/<drive>/` path is refused before the bridge
  is called.
- **Directories.** `benchmark_runs/` is gitignored and need not exist: the stages create
  attempt directories, and the native export creates `benchmark_runs/plan3-part1/bases/`
  and `.../exports/<attempt>/` on the Windows side. Nothing else needs pre-creating.
- **Attempt directories live under `benchmark_runs/plan3-part1/`.** The gate scans only
  that root; the stage CLI refuses an attempt directory or a `--predecessor-journal`
  anywhere else before the journal opens.
- **One FireTuner owner.** Kill any session `civ-mcp` server first; acquire the game per
  the arena live playbook. Only the commands below talk to the game.

## 1. Positive-control timing probe (live, no authoring clock)

```bash
uv run python -m civ_mcp.arena.benchmark_capture_probe \
  --position benchmarks/positions/builder-posctrl-v1.yaml --samples 20 \
  --output-dir benchmark_runs/plan3-part1/capture-probe \
  --write-provenance benchmarks/provenance/plan3-part1-capture-probe.json
```

Outputs: samples, `summary.json` and `evidence-index.json` under the output dir; the
provenance record under `benchmarks/provenance/`. A rerun uses a new output dir. The gate
re-verifies the record against its indexed raw evidence (every sample, the summary and
the index must exist, hash-match and be Git-tracked), so force-add the output dir in
step 4 and never edit it.

## 2. Offline preflight

Re-run the full suite first if code changed (it must pass under the current
`code_identity`; results live in `benchmark_runs/plan3-part1/preflight/`), then:

```bash
uv run python -m civ_mcp.arena.benchmark_authoring preflight \
  --recipes benchmarks/recipes/plan3-builder-a1.yaml benchmarks/recipes/plan3-city-a1.yaml \
            benchmarks/recipes/plan3-tactical-a1.yaml \
  --output benchmarks/provenance/plan3-part1-offline-preflight.json
```

`--probe PATH` overrides the probe provenance; the bound path is recorded and the gate
reads exactly that file. `toolkit_identity` is recorded for information only.

## 3. Each family (builder, city, tactical)

The clock (10,800 s, never paused) opens at the first `survey` and keeps running
across restarts. `survey`/`apply`/`probe` may repeat; the rest run once each:

```bash
R=benchmarks/recipes/plan3-builder-a1.yaml; A=benchmark_runs/plan3-part1/builder-a1
for s in survey apply probe archive capture verify menu-check validate finish; do
  uv run python -m civ_mcp.arena.benchmark_authoring $s --recipe $R --attempt-dir $A || break
done
```

Outputs: `$A/` (journal, `stages/`, `observations/`, `samples/`, `mutations/`,
`probes/`, `validation/`, then `evidence-index.json`); archive under `benchmarks/saves/`;
position, scripts, cases, suite and provenance packet under `benchmarks/`. The packet
lands at the recipe's `outputs.provenance_packet` for the archived version
(`benchmarks/provenance/plan3-<family>-a1-v<N>.json`).

- **Archive stage and stale saves:** `archive` records the native save's
  size/mtime signature (`stat-save`) before it saves, and the export waits out any
  same-named file still carrying that signature (a leftover of an earlier attempt) instead
  of archiving it; the export itself lands at a stage-unique path under the Windows
  checkout's `benchmark_runs/plan3-part1/exports/<attempt>/`, so no leftover can collide.
- **Recovery:** if `finish` or `abandon` fails after the journal closed (index and/or
  packet write), re-run the same command: an index that was already written is verified
  and reused, and only the missing index/packet is written. A re-run after everything
  exists reports the attempt as closed.
- **Abandon** a failed or expired attempt (offline, indexes it):
  `uv run python -m civ_mcp.arena.benchmark_authoring abandon --recipe $R --attempt-dir $A --reason "..."`
- **Substitution** (once per family, only after the predecessor is terminal-failed,
  i.e. finished-failed or abandoned): write the `a2` recipe with `predecessor`,
  `substitution_reason` and `material_change`, then run it in a **new** attempt dir
  under `benchmark_runs/plan3-part1/`. The failed predecessor's journal is found among
  sibling attempt dirs automatically; name it explicitly when needed, and it too must live
  under `benchmark_runs/plan3-part1/` (the gate reads the predecessor from that journal
  and refuses an imported reference to any other):
  `... survey --recipe benchmarks/recipes/plan3-builder-a2.yaml --attempt-dir benchmark_runs/plan3-part1/builder-a2 --predecessor-journal benchmark_runs/plan3-part1/builder-a1/authoring-journal.json`.
  A third identity in a family, across all sibling journals, is refused.
- **Revalidation** (amendment 2026-10-10; only with a recorded prospective amendment
  naming the predecessor packet): a passed packet captured under an earlier identity is
  repeated in a **new** attempt dir with a new clock, same recipe at a bumped `version`
  (new archive and packet names), declaring the sibling journal that holds the scenario as
  passed:
  `... survey --recipe benchmarks/recipes/plan3-city-a2.yaml --attempt-dir benchmark_runs/plan3-part1/city-a2-reval --revalidates benchmark_runs/plan3-part1/city-a2/authoring-journal.json`.
  The reference is journaled (`revalidates: {journal, sha256}`), only that journal's
  "already journaled" refusal is waived, later stages resume without the flag, and the
  passed attempt is left untouched (the gate still lists it). One revalidation per scenario:
  a scenario held by two sibling journals is refused.
- **Setup Lua runs in GameCore** (`GameConnection.execute_mutation`, never re-sent on a
  dead socket): recipes may use the GameCore mutation APIs the verified journals used
  (`UnitManager.InitUnit/PlaceUnit/RestoreMovement`, `ImprovementBuilder.*`, treasury
  and city mutators). Every operation is proved by its readback; an unavailable API fails
  `apply` with the Lua error in the mutations evidence, minutes into the clock.

**`evidence_index_complete`:** after `finish`/`abandon` nothing new may appear in an
attempt dir — any unindexed file there fails the gate. Write scratch output (notes,
`evidence-files` lists, gate drafts) **outside** `benchmark_runs/plan3-part1/<attempt>/`.

## 4. Force-add the evidence

`benchmark_runs/` is ignored; add only the finite closed inventory:

```bash
uv run python -m civ_mcp.arena.benchmark_authoring evidence-files \
  --root benchmark_runs/plan3-part1 --output /tmp/plan3-evidence-paths.txt
git add -f --pathspec-from-file=/tmp/plan3-evidence-paths.txt
git add -f benchmark_runs/plan3-part1/capture-probe   # the probe's indexed raw evidence
git add benchmarks/
xargs -a /tmp/plan3-evidence-paths.txt git ls-files --error-unmatch -- >/dev/null
```

`evidence-files` refuses while any attempt is unindexed (finish or abandon it first).
The timing-probe output dir is not an attempt: add it explicitly (the gate requires every
file its `evidence-index.json` lists to be Git-tracked).

## 5. Gate and exit

```bash
uv run python -m civ_mcp.arena.benchmark_authoring gate \
  --packets benchmarks/provenance/plan3-builder-a1-v1.json \
            benchmarks/provenance/plan3-city-a1-v1.json \
            benchmarks/provenance/plan3-tactical-a1-v1.json \
  --output benchmarks/provenance/plan3-part1-exit.json
```

Use the actual packet paths (an `a2` packet where a substitution passed). Exit 0 only when
every named requirement passes; then run the full suite again into
`benchmark_runs/plan3-part1/exit/pytest.txt` and follow plan Task 22 (report
regeneration from a temporary checkout, exit report, release). A scoring-chain code
change after packets exist means a new `contract_identity` and revalidation
(`benchmarks/contracts/instrument-v2.md`, "Amendments"); a toolkit change does not.
