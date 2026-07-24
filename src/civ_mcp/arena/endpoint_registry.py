"""Lazy registry-backed arena gateway resolution."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from civ_mcp._vendor import brothereye_registry

_SNAPSHOT = Path(__file__).resolve().parents[1] / "_vendor" / "endpoints.json"


@lru_cache(maxsize=1)
def _registry() -> brothereye_registry.Registry:
    if os.environ.get("CIV6_REGISTRY_OFFLINE") == "1":
        return brothereye_registry.load_snapshot(_SNAPSHOT)
    return brothereye_registry.load(fallback=_SNAPSHOT)


def resolve_gateway(endpoint_id: str) -> str:
    registry = _registry()
    try:
        return registry.openai_url(endpoint_id, network="lan")
    except KeyError:
        available = ", ".join(registry.openai_endpoint_ids())
        raise SystemExit(
            f"unknown registry endpoint id {endpoint_id!r}; "
            f"available ids: {available}") from None
