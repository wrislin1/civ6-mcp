#!/usr/bin/env python3
"""Task 7 preflight: verify the local model server emits structured tool_calls.

Mirrors how the arena's in-process LLMPolicy (src/civ_mcp/arena/agent.py) calls the
models — OpenAI-format tools, tool_choice="auto" — and asserts each model returns a
structured `tool_calls` array, not just prose describing a call. A model that returns
no tool_calls is the llama.cpp `--jinja` template gap: STOP and fix the server template;
do NOT fall back to Ollama (the arena targets llama.cpp at :11444 by design).

Stdlib-only (urllib) so it runs anywhere with python3 and no venv. Exit 0 = all pass,
1 = at least one model did not emit tool_calls (or a request failed).

Examples
--------
    # defaults: the two arena baseline models against the LAN llama-swap gateway
    ./preflight-tool-calling.py

    # custom gateway / models
    ./preflight-tool-calling.py --gateway-url http://192.168.20.196:11444/v1 \
        --model gemma4-26b --model qwen3.6-27b
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_GATEWAY = "http://192.168.20.196:11444/v1"
DEFAULT_MODELS = ["gemma4-26b", "qwen3.6-27b"]

# The same shape LLMPolicy sends: a no-arg observation tool + a prompt that should
# trigger exactly one call. tool_choice="auto" so we test the model's own decision
# to emit a tool_call (the real arena path), not a forced call.
TOOLS = [{
    "type": "function",
    "function": {
        "name": "get_overview",
        "description": "Empire/turn overview for your civ",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}]
MESSAGES = [
    {"role": "system",
     "content": "You are playing a civ in Civ VI. Use tools to observe, then act. Keep it brief."},
    {"role": "user",
     "content": "It is turn 1. First, check your empire overview using the get_overview tool."},
]


def _chat_url(gateway_url: str) -> str:
    return gateway_url.rstrip("/") + "/chat/completions"


def smoke(chat_url: str, model: str, api_key: str, timeout: float):
    body = json.dumps({
        "model": model, "messages": MESSAGES, "tools": TOOLS,
        "tool_choice": "auto", "temperature": 0, "max_tokens": 256,
    }).encode()
    req = urllib.request.Request(
        chat_url, data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            obj = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return (model, "HTTP_ERROR", f"{e.code} {e.read()[:300]!r}")
    except Exception as e:  # noqa: BLE001 — preflight reports any failure, never raises
        return (model, "ERROR", repr(e))
    try:
        msg = obj["choices"][0]["message"]
    except Exception:  # noqa: BLE001
        return (model, "BAD_SHAPE", json.dumps(obj)[:300])
    tcs = msg.get("tool_calls") or []
    if tcs:
        names = [tc.get("function", {}).get("name") for tc in tcs]
        return (model, "PASS", f"tool_calls={names}")
    return (model, "NO_TOOL_CALLS", f"content={(msg.get('content') or '')[:200]!r}")


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="preflight-tool-calling.py",
        description="Task 7 preflight: assert each local model emits structured tool_calls.")
    ap.add_argument("--gateway-url", default=DEFAULT_GATEWAY,
                    help=f"OpenAI-compatible base URL ending in /v1 (default: {DEFAULT_GATEWAY})")
    ap.add_argument("--model", action="append", dest="models", default=None,
                    help=f"model slug to test (repeatable; default: {' '.join(DEFAULT_MODELS)})")
    ap.add_argument("--api-key", default="x", help="bearer token (default: x — llama-swap ignores it)")
    ap.add_argument("--timeout", type=float, default=180.0,
                    help="per-request timeout seconds; first call loads the GGUF (default: 180)")
    args = ap.parse_args()

    models = args.models or DEFAULT_MODELS
    chat_url = _chat_url(args.gateway_url)
    print(f"preflight: {chat_url}  models={models}")
    rc = 0
    for m in models:
        model, status, detail = smoke(chat_url, m, args.api_key, args.timeout)
        print(f"[{status}] {model}: {detail}")
        if status != "PASS":
            rc = 1
    print("VERDICT:", "ALL PASS" if rc == 0
          else "STOP — a model did not emit tool_calls (fix the server template; do NOT use Ollama)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
