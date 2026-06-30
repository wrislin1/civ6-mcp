"""Shared tool-name vocabulary constants for the arena driver and analysis pipeline.

Pure constants — no heavy imports — so analyze.py stays offline-pure.
"""
from __future__ import annotations

MCP_CIV6_PREFIX = "mcp__civ6__"

LOCAL_TOOL_VERBS: dict[str, str] = {
    "move_unit":    "move",
    "skip_unit":    "skip",
    "fortify_unit": "fortify",
    "found_city":   "found_city",
}
