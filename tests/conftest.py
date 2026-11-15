"""On a clean clone the NASA terrain, Earth tiles and trained models are not downloaded.

Tests that need them skip with a message instead of failing; run ``make data`` and ``make train`` first
to run everything. Set ``MOONMESH_REQUIRE_DATA=1`` (the release check) to turn every skip into a failure,
so a run with missing data, models or test dependencies can never look like a clean pass.

The conversion happens when the report is made, not around the test call, because a missing file can surface in
two places a ``pytest_runtest_call`` wrapper never sees: during setup (``setUpClass``, ``setUp``, fixtures), and
inside ``unittest.TestCase`` methods, whose exceptions unittest catches itself and pytest only attaches to the
report afterwards.
"""
import os

import pytest

MARKERS = ("Run python -m", "is missing", ".moonmesh-data", "No such file or directory")
REQUIRE_DATA = os.environ.get("MOONMESH_REQUIRE_DATA") == "1"


def missing_data(error):
    """The FileNotFoundError saying downloaded data or a trained model is absent, or None.

    Follows explicit ``raise ... from`` causes, so an ``HTTPException(503)`` raised from a missing kernel counts too.
    Implicit context is not followed: a different error raised while handling a missing file is a real failure.
    """
    seen = set()
    while error is not None and id(error) not in seen:
        if isinstance(error, FileNotFoundError) and any(m in str(error) for m in MARKERS):
            return error
        seen.add(id(error))
        error = error.__cause__
    return None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if REQUIRE_DATA:
        if report.skipped and not hasattr(report, "wasxfail"):
            report.outcome = "failed"
            report.longrepr = f"skipped in release mode (MOONMESH_REQUIRE_DATA=1): {report.longrepr}"
        return
    error = missing_data(call.excinfo.value) if call.excinfo and call.when in ("setup", "call") else None
    if error is not None and report.failed:
        path, line = item.reportinfo()[:2]
        report.outcome = "skipped"
        report.longrepr = (os.fspath(path), None if line is None else line + 1,
                           f"Skipped: needs downloaded data or trained models: {error}")
