"""Lazy registry-backed arena gateway resolution."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from civ_mcp._vendor import brothereye_registry

_SNAPSHOT = Path(__file__).resolve().parents[1] / "_vendor" / "endpoints.json"
_FALSE_ENV_VALUES = frozenset({"0", "false", "no", "off"})


def _env_flag(name: str) -> bool:
    raw = os.environ.get(name)
    return raw is not None and raw.strip().lower() not in _FALSE_ENV_VALUES


@lru_cache(maxsize=2)
def _registry(snapshot_only: bool = False) -> brothereye_registry.Registry:
    return brothereye_registry.load_for_resolution(
        fallback=_SNAPSHOT,
        snapshot_only=(snapshot_only or _env_flag("CIV6_REGISTRY_OFFLINE")),
    )


def resolve_gateway(endpoint_id: str, *, snapshot_only: bool = False) -> str:
    return brothereye_registry.resolve_openai_url(
        _registry(snapshot_only), endpoint_id, network="lan"
    )
