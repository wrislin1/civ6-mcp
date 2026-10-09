#!/usr/bin/env bash
# usage: benchmark-family-chain.sh <recipe> <attempt-dir> <stage>...
# Runs stages in order, stopping at the first failure. After a passing `archive` stage
# it commits the new benchmarks/saves/*.Civ6Save and fast-forwards the Windows checkout
# so `capture` can deploy the archive through the bridge.
# ARCHIVE_COMMIT_TRAILER (optional) is appended to the commit message, e.g. a Co-Authored-By line.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
R="$1"; A="$2"; shift 2
for S in "$@"; do
  echo "=== $(date -u +%H:%M:%S) stage $S"
  "$HERE/benchmark-stage.sh" "$R" "$A" "$S" | grep -a 'STAGE EXIT'
  rc=${PIPESTATUS[0]}
  if [[ $rc -ne 0 ]]; then echo "CHAIN STOPPED at $S (exit $rc)"; exit "$rc"; fi
  if [[ "$S" == "archive" ]]; then
    mapfile -t NEW < <(git status --short --untracked-files=all -- benchmarks/saves/ | awk '{print $2}')
    if [[ ${#NEW[@]} -ne 1 ]]; then
      echo "expected exactly one new save under benchmarks/saves/, found ${#NEW[@]}" >&2; exit 1
    fi
    git add "${NEW[0]}"
    git commit -q -m "feat(benchmark): archive $(basename "${NEW[0]}" .Civ6Save)

Native export of the archived start published by the archive stage of $A.${ARCHIVE_COMMIT_TRAILER:+

$ARCHIVE_COMMIT_TRAILER}"
    echo "committed ${NEW[0]} $(git rev-parse --short HEAD)"
    "$HERE/sync-windows-checkout.sh" || exit 1
  fi
done
echo "CHAIN DONE"
