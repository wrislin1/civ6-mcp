#!/usr/bin/env python3
"""Classify the files changed in a git range as fingerprint, toolkit or other, and print
the current implementation and toolkit fingerprints next to the preflight record.

usage: uv run python tools/skills/civ6-arena-live/scripts/identity-impact.py [RANGE]
           [--preflight benchmarks/provenance/plan3-part1-offline-preflight.json]

Only FINGERPRINT_DEPENDENCIES move code_identity (a fingerprint change after a packet
exists forces that family to repeat from `survey`); TOOLKIT_DEPENDENCIES never do.
Run this BEFORE writing a contract amendment or deciding whether a family must rerun.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from civ_mcp.arena.benchmark_contract_v2 import (
    FINGERPRINT_DEPENDENCIES,
    TOOLKIT_DEPENDENCIES,
    implementation_fingerprint,
    toolkit_fingerprint,
)


def _files(root: Path, *args: str) -> set[str]:
    out = subprocess.check_output(["git", "diff", "--name-only", *args], cwd=root, text=True)
    return set(out.split())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("range", nargs="?", default="HEAD~1..HEAD")
    ap.add_argument("--preflight", type=Path,
                    default=Path("benchmarks/provenance/plan3-part1-offline-preflight.json"))
    args = ap.parse_args()
    root = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                        text=True).strip())
    fp, tk = set(FINGERPRINT_DEPENDENCIES), set(TOOLKIT_DEPENDENCIES)
    moves = False
    for label, files in ((f"range {args.range}", _files(root, args.range)),
                         ("working tree vs HEAD", _files(root, "HEAD"))):
        if not files:
            continue
        print(f"{label}:")
        for f in sorted(files):
            if f in fp:
                kind, moves = "FINGERPRINT (moves code_identity)", True
            elif f in tk:
                kind = "toolkit (informational only)"
            else:
                kind = "other"
            print(f"  {kind:36} {f}")
    verdict = ("code_identity MOVES; captured packets go stale" if moves
               else "code_identity unchanged")
    print(f"verdict: {verdict}")
    print(f"current code_identity    {implementation_fingerprint(root)}")
    print(f"current toolkit_identity {toolkit_fingerprint(root)}")
    pre = root / args.preflight
    if pre.is_file():
        rec = json.loads(pre.read_text())
        print(f"preflight code_identity  {rec.get('code_identity')}  ({args.preflight})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
