# Whole-Branch Fix Report

Date: 2026-07-27

## Finding Dispositions

1. Capture completion integrity: fixed.
   - `build_capture_lua()` emits
     `CAPABILITY|DiplomaticActions|__MCP_CAPTURE_COMPLETE__` only after all
     three table loops.
   - `parse_capture_lines()` requires exactly one completion marker and
     requires it to be the final record.
   - The marker is removed from the parsed snapshot. No action counts are
     pinned, and the transport sentinel remains unrelated.
   - Regressions reject a three-table prefix without completion and exercise
     `main(["--capture"])` to prove the prior snapshot remains unchanged.

2. Capability truth: fixed.
   - `UNITCOMMAND_MOVE_JUMP` is `missing/low`, grounded in the shipped
     player-visible Expansion 2 Jump UI path.
   - `UNITCOMMAND_PRIORITY_TARGET` is `missing/high`, grounded in the shipped
     player-visible protected-Support targeting path.
   - Final evidence is `63 covered / 59 missing / 11 excluded / 133 total`;
     all 133 snapshot actions remain classified.

3. `BUILD_ROUTE` affordances: fixed.
   - MCP narration emits
     `unit_action(unit_id=..., action="build_route")`.
   - Arena narration suppresses an exact route call because no arena route
     tool exists.
   - Direct MCP/arena and opening-briefing regressions preserve normal
     improvement narration alongside the pseudo-token.

4. Minimal/full briefing matrix: fixed.
   - Explicit `TIERS["minimal"]` and `TIERS["full"]` briefing regressions now
     sit alongside standard, MCP, and no-context coverage.

## TDD Evidence

Command:

```text
uv run pytest -q tests/arena/test_capability_coverage.py tests/test_narrate_units.py tests/arena/test_briefing.py
```

RED output after collection was corrected to exercise behavior:

```text
16 failed, 77 passed in 0.73s
```

GREEN output:

```text
93 passed in 1.19s
```

## Verification

Focused capability, narration, briefing, registry, and audit tests:

```text
$ uv run pytest -q tests/arena/test_capability_coverage.py tests/test_narrate_units.py tests/arena/test_briefing.py tests/arena/test_registry.py tests/arena/test_tool_coverage_audit.py
197 passed in 1.54s
```

Offline capability audit:

```text
$ uv run python scripts/audit_civ6_capabilities.py
counts: covered=63 missing=59 excluded=11 total=133
```

The ranked output includes:

```text
high  UNITCOMMAND_PRIORITY_TARGET
low   UNITCOMMAND_MOVE_JUMP
```

Offline registry audit:

```text
$ uv run python scripts/audit_arena_tool_coverage.py
counts: registry=90 minimal=15 standard=28 full=90
MCP unit actions absent from CLAUDE.md: []
MCP unit actions absent from arena: ['build_route', 'delete', 'remove_improvement', 'sacrifice_charges', 'sleep']
```

Static checks:

```text
$ uv run python -m compileall -q scripts/audit_civ6_capabilities.py src/civ_mcp/capability_map.py src/civ_mcp/narrate.py
(no output; exit 0)

$ git diff --check
(no output; exit 0)
```

Full arena suite, run as the last test command:

```text
$ uv run pytest -q tests/arena
1875 passed in 144.03s (0:02:24)
```

The repository environment does not include Ruff:

```text
$ uv run ruff check scripts/audit_civ6_capabilities.py src/civ_mcp/capability_map.py src/civ_mcp/narrate.py tests/arena/test_capability_coverage.py tests/test_narrate_units.py tests/arena/test_briefing.py
error: Failed to spawn: `ruff`
  Caused by: No such file or directory (os error 2)
```

## Evidence Inspection

- `docs/research/civ6-action-space.json` remains byte-identical to `HEAD`,
  contains all 133 actions, and contains no completion marker.
- `docs/research/arena-tool-coverage-audit.md` is unchanged because registry
  and tier membership remain `90 / 15 / 28 / 90`.
- Both offline audit CLIs were inspected after the code changes.

## Self-Review

- The completion marker keeps the approved three-field record shape and is
  observable independently of connection timeout/sentinel behavior.
- Parser validation is content-based and does not assume current table sizes.
- Marker handling cannot silently add a pseudo-action to the committed
  snapshot.
- `BUILD_ROUTE` is handled before generic improvement rendering, while
  `UNKNOWN` and ordinary improvements retain their prior behavior.
- The diff is limited to the three production modules, their focused tests,
  briefing matrix coverage, and this report. Serena metadata and generated
  snapshots are not included.

## Commit

This report is included in the single whole-branch fix commit:

```text
fix(arena): close capability visibility review
```

## Concerns

- Ruff could not be run because the executable is absent from the project
  environment.
- Capture completion was verified through generated Lua, parser, and atomic
  CLI regressions; no live FireTuner capture was performed in this offline fix
  wave.
