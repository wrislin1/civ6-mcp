#!/usr/bin/env bash
# usage: benchmark-stage.sh <recipe> <attempt-dir> <stage> [extra stage args...]
# Runs one authoring stage with INFO logging (the CLI configures none, so a 10-20 min
# stage is otherwise silent), drops the per-command connection chatter, and tees a log
# OUTSIDE the attempt dir (evidence_index_complete rejects scratch inside it).
# `grep -a` is required: the tuner/bridge stream carries bytes that make grep call the
# stream binary, which leaves the tee'd log empty.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"
R="$1"; A="$2"; S="$3"; shift 3
LOG_DIR="${BENCHMARK_STAGE_LOG_DIR:-benchmark_runs/logs}"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$(basename "$A")-$S-$(date -u +%Y%m%dT%H%M%SZ).log"
PROG='
import logging, sys
logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
from civ_mcp.arena.benchmark_authoring import main
stage, recipe, attempt, *extra = sys.argv[1:]
raise SystemExit(main([stage, "--recipe", recipe, "--attempt-dir", attempt, *extra]))
'
uv run python -c "$PROG" "$S" "$R" "$A" "$@" 2>&1 \
  | grep -a -v 'INFO civ_mcp.connection' | tee "$LOG"
rc=${PIPESTATUS[0]}
echo "STAGE EXIT: $rc (log: $LOG)"
exit "$rc"
