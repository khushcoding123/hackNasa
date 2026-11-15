"""On a clean clone the NASA terrain, Earth tiles and trained models are not downloaded.

Tests that need them skip with a message instead of failing; run ``make data`` and ``make train`` first
to run everything.
"""
import pytest

MARKERS = ("Run python -m", "is missing", ".moonmesh-data", "No such file or directory")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    outcome = yield
    error = outcome.excinfo[1] if outcome.excinfo else None
    if isinstance(error, FileNotFoundError) and any(m in str(error) for m in MARKERS):
        outcome.force_exception(pytest.skip.Exception(f"needs downloaded data or trained models: {error}"))
