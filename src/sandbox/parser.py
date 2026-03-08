"""Pytest output parser — extract structured test results from raw output.

Pure functions, no OpenSandbox dependency.
"""

from __future__ import annotations

import json
import re

from .models import TestFailure, TestResults


def parse_pytest_output(raw: str) -> TestResults:
    """Parse pytest terminal output into structured :class:`TestResults`.

    Handles the standard summary line format::

        === 3 passed, 1 failed, 2 skipped in 0.42s ===
        === 10 passed in 1.23s ===

    Also extracts FAILED lines like::

        FAILED tests/test_foo.py::test_bar - AssertionError: expected 1
    """
    total = 0
    passed = 0
    failed = 0
    skipped = 0

    # Match the summary line: "X passed", "X failed", "X skipped", "X error"
    summary_pattern = re.compile(
        r"=+\s+(.*?)\s+in\s+[\d.]+s\s*=+",
        re.MULTILINE,
    )
    match = summary_pattern.search(raw)
    if match:
        summary = match.group(1)
        for count_match in re.finditer(r"(\d+)\s+(passed|failed|skipped|error)", summary):
            count = int(count_match.group(1))
            kind = count_match.group(2)
            if kind == "passed":
                passed = count
            elif kind == "failed":
                failed = count
            elif kind == "skipped":
                skipped = count
            elif kind == "error":
                failed += count
        total = passed + failed + skipped

    # Extract individual FAILED lines for structured feedback
    failures: list[TestFailure] = []
    failed_pattern = re.compile(r"^FAILED\s+(\S+?)(?:\s+-\s+(.*))?$", re.MULTILINE)
    for fail_match in failed_pattern.finditer(raw):
        test_name = fail_match.group(1)
        message = fail_match.group(2) or ""
        failures.append(
            TestFailure(
                test_name=test_name,
                message=message,
                structured_error={"source": "pytest_terminal"},
            )
        )

    return TestResults(
        total=total,
        passed=passed,
        failed=failed,
        skipped=skipped,
        failures=failures,
    )


def parse_pytest_json_report(json_str: str) -> TestResults:
    """Parse ``pytest-json-report`` JSON output into structured :class:`TestResults`.

    Expects the JSON format produced by ``pytest --json-report``::

        {
            "summary": {"passed": 3, "failed": 1, ...},
            "tests": [
                {"nodeid": "...", "outcome": "passed|failed|skipped", ...},
                ...
            ]
        }
    """
    data = json.loads(json_str)

    summary = data.get("summary", {})
    passed = summary.get("passed", 0)
    failed = summary.get("failed", 0)
    skipped = summary.get("skipped", 0)
    error = summary.get("error", 0)
    total = summary.get("total", passed + failed + skipped + error)

    failures: list[TestFailure] = []
    for test in data.get("tests", []):
        if test.get("outcome") in ("failed", "error"):
            call_info = test.get("call", {})
            longrepr = call_info.get("longrepr", "")
            crash = call_info.get("crash", {})
            failures.append(
                TestFailure(
                    test_name=test.get("nodeid", "unknown"),
                    message=crash.get("message", str(longrepr)[:500]),
                    structured_error={
                        "source": "pytest_json_report",
                        "lineno": crash.get("lineno"),
                        "path": crash.get("path"),
                        "longrepr": str(longrepr)[:2000],
                    },
                )
            )

    return TestResults(
        total=total,
        passed=passed,
        failed=failed + error,
        skipped=skipped,
        failures=failures,
    )
