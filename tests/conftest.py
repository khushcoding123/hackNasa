"""On a clean clone the NASA terrain, Earth tiles and trained models are not downloaded.

Tests that need them skip with a message instead of failing; run ``make data`` and ``make train`` first
to run everything. Set ``MOONMESH_REQUIRE_DATA=1`` (the release check) to turn every skip into a failure,
so a run with missing data, models or test dependencies can never look like a clean pass.
"""
import os

import pytest

MARKERS = ("Run python -m", "is missing", ".moonmesh-data", "No such file or directory")
REQUIRE_DATA = os.environ.get("MOONMESH_REQUIRE_DATA") == "1"


def needs_data(error):
    return isinstance(error, FileNotFoundError) and any(m in str(error) for m in MARKERS)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    outcome = yield
    error = outcome.excinfo[1] if outcome.excinfo else None
    if needs_data(error) and not REQUIRE_DATA:
        outcome.force_exception(pytest.skip.Exception(f"needs downloaded data or trained models: {error}"))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if REQUIRE_DATA and report.skipped and not hasattr(report, "wasxfail"):
        report.outcome = "failed"
        report.longrepr = f"skipped in release mode (MOONMESH_REQUIRE_DATA=1): {report.longrepr}"
    elif not REQUIRE_DATA and report.failed and call.excinfo is not None and needs_data(call.excinfo.value):
        # unittest.TestCase failures and setUpClass errors reach pytest here, not in pytest_runtest_call,
        # so the missing-data skip above never sees them.
        report.outcome = "skipped"
        report.longrepr = (str(item.path), item.location[1] + 1,
                           f"Skipped: needs downloaded data or trained models: {call.excinfo.value}")
