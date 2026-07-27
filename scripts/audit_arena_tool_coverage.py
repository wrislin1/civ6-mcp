#!/usr/bin/env python3
"""Generate deterministic arena/MCP tool-coverage evidence."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
from typing import Iterable

from civ_mcp.arena.registry import TIERS, TOOL_REGISTRY


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = REPO_ROOT / "src" / "civ_mcp" / "server.py"
GAME_STATE_PATH = REPO_ROOT / "src" / "civ_mcp" / "game_state.py"
REGISTRY_PATH = REPO_ROOT / "src" / "civ_mcp" / "arena" / "registry.py"
CLAUDE_PATH = REPO_ROOT / "CLAUDE.md"

ACTION_ALIASES = {
    "activate_great_person": "activate",
    "start_trade_route": "trade_route",
    "teleport_trader": "teleport",
}


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _is_mcp_tool(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "mcp"
            and target.attr == "tool"
        ):
            return True
    return False


def _only(nodes: Iterable[ast.AST], what: str) -> ast.AST:
    """First matching node, with a readable error when the source moved."""
    node = next(iter(nodes), None)
    if node is None:
        raise SystemExit(f"audit: could not find {what}; update this script")
    return node


def _game_state_methods(tree: ast.Module) -> set[str]:
    game_state = _only(
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "GameState"
        ),
        "class GameState in game_state.py",
    )
    return {
        node.name
        for node in game_state.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _gs_attributes(nodes: Iterable[ast.AST], methods: set[str]) -> set[str]:
    return {
        child.attr
        for node in nodes
        for child in ast.walk(node)
        if isinstance(child, ast.Attribute)
        and isinstance(child.value, ast.Name)
        and child.value.id == "gs"
        and child.attr in methods
    }


def _mcp_gamestate_methods(
    server_tree: ast.Module, methods: set[str]
) -> set[str]:
    tools = [
        node
        for node in server_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and _is_mcp_tool(node)
    ]
    return _gs_attributes(tools, methods)


def _match_literals(pattern: ast.pattern) -> set[str]:
    """String literals a case pattern matches, including or-patterns."""
    if isinstance(pattern, ast.MatchOr):
        return {
            literal
            for alternative in pattern.patterns
            for literal in _match_literals(alternative)
        }
    if isinstance(pattern, ast.MatchValue) and isinstance(
        pattern.value, ast.Constant
    ):
        if isinstance(pattern.value.value, str):
            return {pattern.value.value}
    return set()


def _unit_actions(server_tree: ast.Module) -> set[str]:
    unit_action = _only(
        (
            node
            for node in server_tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "unit_action"
        ),
        "async def unit_action in server.py",
    )
    action_match = _only(
        (
            node
            for node in ast.walk(unit_action)
            if isinstance(node, ast.Match)
            and isinstance(node.subject, ast.Call)
            and isinstance(node.subject.func, ast.Attribute)
            and isinstance(node.subject.func.value, ast.Name)
            and node.subject.func.value.id == "action"
            and node.subject.func.attr == "lower"
        ),
        "match action.lower() in unit_action",
    )
    return {
        literal
        for case in action_match.cases
        for literal in _match_literals(case.pattern)
    }


def _tier_action_verbs_absent(tier: str) -> list[str]:
    all_registry_verbs = {
        tool.verb for tool in TOOL_REGISTRY.values() if tool.verb
    }
    tier_reachable_verbs = {
        TOOL_REGISTRY[name].verb
        for name in TIERS[tier]
        if TOOL_REGISTRY[name].verb
    }
    return sorted(all_registry_verbs - tier_reachable_verbs)


def collect_evidence() -> dict[str, object]:
    server_tree = _parse(SERVER_PATH)
    registry_tree = _parse(REGISTRY_PATH)
    methods = _game_state_methods(_parse(GAME_STATE_PATH))
    mcp_methods = _mcp_gamestate_methods(server_tree, methods)
    registry_methods = _gs_attributes(registry_tree.body, methods)
    server_actions = _unit_actions(server_tree)

    claude_text = CLAUDE_PATH.read_text(encoding="utf-8")
    table = claude_text.split("## Unit Actions Reference", 1)[1].split(
        "Common improvements:",
        1,
    )[0]
    documented_actions = set(
        re.findall(r"^\| `([^`]+)` \|", table, re.MULTILINE)
    )
    arena_actions = {
        ACTION_ALIASES.get(tool.verb, tool.verb)
        for tool in TOOL_REGISTRY.values()
        if tool.verb
    }

    return {
        "counts": {
            "registry": len(TOOL_REGISTRY),
            "minimal": len(TIERS["minimal"]),
            "standard": len(TIERS["standard"]),
            "full": len(TIERS["full"]),
        },
        "mcp_unit_actions": sorted(server_actions),
        "mcp_unit_actions_absent_from_claude": sorted(
            server_actions - documented_actions
        ),
        "mcp_unit_actions_absent_from_arena": sorted(
            server_actions - arena_actions
        ),
        "mcp_gamestate_methods": sorted(mcp_methods),
        "mcp_gamestate_methods_absent_from_arena": sorted(
            mcp_methods - registry_methods
        ),
        "tier_action_verbs_absent": {
            "minimal": _tier_action_verbs_absent("minimal"),
            "standard": _tier_action_verbs_absent("standard"),
        },
    }


def _print_human(evidence: dict[str, object]) -> None:
    counts = evidence["counts"]
    assert isinstance(counts, dict)
    print(
        "counts:",
        f"registry={counts['registry']}",
        f"minimal={counts['minimal']}",
        f"standard={counts['standard']}",
        f"full={counts['full']}",
    )
    print(
        "MCP unit actions absent from CLAUDE.md:",
        evidence["mcp_unit_actions_absent_from_claude"],
    )
    print(
        "MCP unit actions absent from arena:",
        evidence["mcp_unit_actions_absent_from_arena"],
    )
    print(
        "MCP-reached GameState methods absent from arena registry:",
        evidence["mcp_gamestate_methods_absent_from_arena"],
    )
    tier_absent = evidence["tier_action_verbs_absent"]
    print("Minimal action verbs absent:", tier_absent["minimal"])
    print("Standard action verbs absent:", tier_absent["standard"])
    print()
    print(
        "| tool | minimal | minimal disposition | standard | "
        "standard disposition | full |"
    )
    print("|---|---:|---|---:|---|---:|")
    for name in TOOL_REGISTRY:
        in_minimal = name in TIERS["minimal"]
        in_standard = name in TIERS["standard"]
        minimal_disposition = (
            "present" if in_minimal else "intentionally-excluded"
        )
        standard_disposition = (
            "present" if in_standard else "listed-for-later"
        )
        print(
            f"| `{name}` | {'yes' if in_minimal else 'no'} | "
            f"{minimal_disposition} | "
            f"{'yes' if in_standard else 'no'} | "
            f"{standard_disposition} | yes |"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable evidence without the tier table.",
    )
    args = parser.parse_args()
    evidence = collect_evidence()
    if args.json:
        print(json.dumps(evidence, sort_keys=True))
    else:
        _print_human(evidence)


if __name__ == "__main__":
    main()
