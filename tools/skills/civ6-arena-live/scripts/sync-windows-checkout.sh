#!/usr/bin/env bash
# Fast-forward the native Windows companion checkout to this WSL checkout's branch
# HEAD without going through GitHub. The bridge runs the Windows checkout's code with
# that checkout as cwd, so repo-relative archive paths (benchmarks/saves/) resolve
# there: run this after every commit that adds a save and before any `capture`.
set -euo pipefail
WSL_REPO="${WSL_REPO:-$(git rev-parse --show-toplevel)}"
WIN_REPO="${WIN_REPO:-/mnt/c/Users/wrisl/dev/civ6-mcp}"
BRANCH="${1:-$(git -C "$WSL_REPO" rev-parse --abbrev-ref HEAD)}"
if [[ -n "$(git -C "$WIN_REPO" status --porcelain --untracked-files=no)" ]]; then
  echo "refusing: Windows checkout has tracked modifications" >&2
  git -C "$WIN_REPO" status --short --untracked-files=no >&2
  exit 1
fi
git -C "$WIN_REPO" fetch -q "$WSL_REPO" "$BRANCH"
git -C "$WIN_REPO" merge -q --ff-only FETCH_HEAD
want=$(git -C "$WSL_REPO" rev-parse "$BRANCH")
have=$(git -C "$WIN_REPO" rev-parse HEAD)
if [[ "$want" != "$have" ]]; then
  echo "mismatch after sync: WSL $BRANCH=$want Windows HEAD=$have" >&2
  exit 1
fi
echo "windows checkout at $(git -C "$WIN_REPO" rev-parse --short HEAD) ($BRANCH)"
