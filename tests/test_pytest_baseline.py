"""Regression coverage for the default local unit-test baseline."""

from pathlib import Path
import tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _pyproject() -> dict:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as pyproject_file:
        return tomllib.load(pyproject_file)


def test_default_pytest_discovery_is_limited_to_unit_tests():
    pytest_options = _pyproject()["tool"]["pytest"]["ini_options"]

    assert pytest_options.get("testpaths") == ["tests"]


def test_dev_environment_includes_async_pytest_plugin():
    dev_dependencies = _pyproject()["dependency-groups"]["dev"]

    assert any(dependency.startswith("pytest-asyncio") for dependency in dev_dependencies)
