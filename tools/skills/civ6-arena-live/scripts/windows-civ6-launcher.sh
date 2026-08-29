#!/usr/bin/env bash
set -euo pipefail

readonly WINDOWS_REPO_WSL="/mnt/c/Users/wrisl/dev/civ6-mcp"
readonly WINDOWS_PYTHON_WSL="/mnt/c/Users/wrisl/AppData/Local/Programs/Python/Python312/python.exe"
readonly WINDOWS_BOOTSTRAP='C:\Users\wrisl\dev\civ6-mcp\tools\windows\civ6_launcher_bootstrap.py'

if [[ ! -x "$WINDOWS_PYTHON_WSL" ]]; then
  echo "Signed Windows Python not found: $WINDOWS_PYTHON_WSL" >&2
  exit 1
fi

if [[ ! -f "$WINDOWS_REPO_WSL/tools/windows/civ6_launcher_bootstrap.py" ]]; then
  echo "Windows companion checkout is missing or stale: $WINDOWS_REPO_WSL" >&2
  exit 1
fi

exec "$WINDOWS_PYTHON_WSL" "$WINDOWS_BOOTSTRAP" "$@"
