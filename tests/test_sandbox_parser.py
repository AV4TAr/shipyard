"""Tests for the pytest output parser."""

from __future__ import annotations

import json

from src.sandbox.parser import parse_pytest_json_report, parse_pytest_output


class TestParsePytestOutput:
    def test_all_passed(self):
        raw = "============================= 10 passed in 0.42s =============================="
        result = parse_pytest_output(raw)
        assert result.total == 10
        assert result.passed == 10
        assert result.failed == 0
        assert result.skipped == 0
        assert result.failures == []

    def test_mixed_results(self):
        raw = "=================== 3 passed, 2 failed, 1 skipped in 1.23s ===================="
        result = parse_pytest_output(raw)
        assert result.total == 6
        assert result.passed == 3
        assert result.failed == 2
        assert result.skipped == 1

    def test_all_failed(self):
        raw = "============================= 5 failed in 0.10s =============================="
        result = parse_pytest_output(raw)
        assert result.total == 5
        assert result.passed == 0
        assert result.failed == 5

    def test_errors_count_as_failed(self):
        raw = "======================== 2 passed, 1 error in 0.50s ========================"
        result = parse_pytest_output(raw)
        assert result.total == 3
        assert result.passed == 2
        assert result.failed == 1

    def test_extracts_failed_lines(self):
        raw = (
            "FAILED tests/test_foo.py::test_bar - AssertionError: expected 1\n"
            "FAILED tests/test_baz.py::test_qux - ValueError: boom\n"
            "==================== 1 passed, 2 failed in 0.30s ===================="
        )
        result = parse_pytest_output(raw)
        assert len(result.failures) == 2
        assert result.failures[0].test_name == "tests/test_foo.py::test_bar"
        assert "AssertionError" in result.failures[0].message
        assert result.failures[1].test_name == "tests/test_baz.py::test_qux"

    def test_failed_line_without_message(self):
        raw = (
            "FAILED tests/test_x.py::test_y\n"
            "============================= 1 failed in 0.10s =============================="
        )
        result = parse_pytest_output(raw)
        assert len(result.failures) == 1
        assert result.failures[0].test_name == "tests/test_x.py::test_y"
        assert result.failures[0].message == ""

    def test_empty_output(self):
        result = parse_pytest_output("")
        assert result.total == 0
        assert result.passed == 0
        assert result.failed == 0
        assert result.failures == []

    def test_no_summary_line(self):
        raw = "some random output\nno summary here"
        result = parse_pytest_output(raw)
        assert result.total == 0


class TestParsePytestJsonReport:
    def test_basic_report(self):
        report = {
            "summary": {"total": 5, "passed": 3, "failed": 1, "skipped": 1},
            "tests": [
                {"nodeid": "tests/test_a.py::test_ok", "outcome": "passed"},
                {"nodeid": "tests/test_a.py::test_skip", "outcome": "skipped"},
                {
                    "nodeid": "tests/test_a.py::test_fail",
                    "outcome": "failed",
                    "call": {
                        "longrepr": "assert 1 == 2",
                        "crash": {
                            "message": "AssertionError: assert 1 == 2",
                            "lineno": 10,
                            "path": "tests/test_a.py",
                        },
                    },
                },
            ],
        }
        result = parse_pytest_json_report(json.dumps(report))
        assert result.total == 5
        assert result.passed == 3
        assert result.failed == 1
        assert result.skipped == 1
        assert len(result.failures) == 1
        assert result.failures[0].test_name == "tests/test_a.py::test_fail"
        assert "AssertionError" in result.failures[0].message
        assert result.failures[0].structured_error["lineno"] == 10

    def test_error_outcome(self):
        report = {
            "summary": {"total": 2, "passed": 1, "error": 1},
            "tests": [
                {"nodeid": "tests/test_b.py::test_ok", "outcome": "passed"},
                {
                    "nodeid": "tests/test_b.py::test_err",
                    "outcome": "error",
                    "call": {"longrepr": "fixture error", "crash": {}},
                },
            ],
        }
        result = parse_pytest_json_report(json.dumps(report))
        assert result.failed == 1
        assert len(result.failures) == 1

    def test_empty_tests(self):
        report = {"summary": {"total": 0, "passed": 0}, "tests": []}
        result = parse_pytest_json_report(json.dumps(report))
        assert result.total == 0
        assert result.failures == []

    def test_missing_call_info(self):
        report = {
            "summary": {"total": 1, "failed": 1},
            "tests": [
                {"nodeid": "tests/test_c.py::test_x", "outcome": "failed"},
            ],
        }
        result = parse_pytest_json_report(json.dumps(report))
        assert len(result.failures) == 1
        assert result.failures[0].test_name == "tests/test_c.py::test_x"
