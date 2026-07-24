"""Registry-backed arena gateway resolution."""
import pytest

from civ_mcp.arena import endpoint_registry
from civ_mcp.arena.endpoint_registry import resolve_gateway


def test_resolves_known_endpoint_from_snapshot():
    assert resolve_gateway("riz-unified-cpp") == "http://192.168.20.196:11444/v1"


def test_resolves_ollama_endpoint_as_openai_base():
    assert resolve_gateway(
        "riz-gpu1") == "http://192.168.20.196:11431/v1"


def test_unknown_id_is_value_error():
    with pytest.raises(ValueError, match="unknown registry endpoint id 'nope'"):
        resolve_gateway("nope")


def test_registry_loads_once_per_process():
    endpoint_registry._registry.cache_clear()
    resolve_gateway("riz-unified-cpp")
    assert endpoint_registry._registry.cache_info().misses == 1
    resolve_gateway("riz-gpu0-cpp")
    assert endpoint_registry._registry.cache_info().misses == 1


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_truthy_offline_values_use_snapshot(monkeypatch, value):
    monkeypatch.setenv("CIV6_REGISTRY_OFFLINE", value)
    endpoint_registry._registry.cache_clear()
    monkeypatch.setattr(
        endpoint_registry.brothereye_registry,
        "load",
        lambda **kwargs: pytest.fail("called live loader"),
    )
    assert resolve_gateway("riz-gpu0-cpp").endswith(":11440/v1")


def test_arena_modules_do_not_load_registry_at_import(tmp_path):
    import os
    import subprocess
    import sys

    env = os.environ.copy()
    env.pop("CIV6_REGISTRY_OFFLINE", None)
    code = """
import urllib.request
calls = []
def blocked(*args, **kwargs):
    calls.append((args, kwargs))
    raise AssertionError("network at import")
urllib.request.urlopen = blocked
import civ_mcp.arena.arena
import civ_mcp.arena.experiment
assert calls == [], calls
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
