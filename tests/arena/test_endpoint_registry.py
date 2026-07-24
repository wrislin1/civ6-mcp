"""Registry-backed arena gateway resolution."""
import pytest

from civ_mcp.arena import endpoint_registry
from civ_mcp.arena.endpoint_registry import resolve_gateway


def test_resolves_known_endpoint_from_snapshot():
    assert resolve_gateway("riz-unified-cpp") == "http://192.168.20.196:11444/v1"


def test_unknown_endpoint_lists_available_ids():
    with pytest.raises(
        SystemExit, match=r"unknown registry endpoint id 'nope'.*available ids:.*riz-unified-cpp"
    ):
        resolve_gateway("nope")


def test_registry_loads_once_per_process():
    endpoint_registry._registry.cache_clear()
    resolve_gateway("riz-unified-cpp")
    assert endpoint_registry._registry.cache_info().misses == 1
    resolve_gateway("riz-gpu0-cpp")
    assert endpoint_registry._registry.cache_info().misses == 1


def test_offline_mode_never_calls_live_loader(monkeypatch):
    endpoint_registry._registry.cache_clear()

    def unexpected(*args, **kwargs):
        raise AssertionError("offline mode called live load")

    monkeypatch.setattr(
        endpoint_registry.brothereye_registry, "load", unexpected)
    assert resolve_gateway("riz-gpu0-cpp") == "http://192.168.20.196:11440/v1"
