"""Finite scripted backend for benchmark validation runs.

`ScriptedBackend` implements the backend `chat(messages, tools) -> Reply`
contract with a fixed, declared sequence of tool-call batches: one batch per
`chat` call. It supplies actions only. It holds no game connection, state
snapshots, rubric, case expectations, or policy repair -- the benchmark agent
dispatches its calls exactly as it would a model's.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from civ_mcp.arena.backends import Reply
from civ_mcp.arena.benchmark_agent import FINISH_TRIAL_TOOL_NAME
from civ_mcp.arena.benchmark_manifest_v2 import validate_v2_document


def batch_reply(script_id, round_index, batch):
    return Reply(text=None, tool_calls=[
        {"id": f"{script_id}:{round_index}:{index}", "name": call["name"],
         "arguments": json.dumps(call["arguments"], sort_keys=True)}
        for index, call in enumerate(batch["calls"])
    ])


class ScriptedBackend:
    """Replays a validated v2 script, one batch per `chat` round."""

    def __init__(self, script: dict[str, Any], *, game_tools: tuple[str, ...]) -> None:
        validate_v2_document(script, kind="script")
        expected_names = [*game_tools, FINISH_TRIAL_TOOL_NAME]
        for bi, batch in enumerate(script["batches"]):
            for ci, call in enumerate(batch["calls"]):
                if call["name"] not in expected_names:
                    raise ValueError(
                        f"script.batches[{bi}].calls[{ci}]: tool {call['name']!r} "
                        f"is not in the declared game tools {list(game_tools)}"
                    )
        self._script_id: str = script["script_id"]
        self._batches: list[dict[str, Any]] = script["batches"]
        self._expected_tool_names = expected_names
        self._round_index = 0

    async def chat(self, messages: Sequence[dict[str, Any]], tools: Sequence[dict[str, Any]]) -> Reply:
        passed_names = [tool["function"]["name"] for tool in tools]
        if passed_names != self._expected_tool_names:
            raise ValueError(
                f"tool schema mismatch: passed {passed_names}, "
                f"script declared {self._expected_tool_names}"
            )
        if self._round_index >= len(self._batches):
            raise ValueError("script exhausted")
        reply = batch_reply(self._script_id, self._round_index, self._batches[self._round_index])
        self._round_index += 1
        return reply

    def assert_exhausted(self) -> None:
        remaining = len(self._batches) - self._round_index
        if remaining:
            raise ValueError(f"script {self._script_id!r} has {remaining} unconsumed batch(es)")
