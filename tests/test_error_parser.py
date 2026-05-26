from __future__ import annotations

from pathlib import Path

from lego.validator import categorize_errors, parse_errors


FIXTURES = Path(__file__).parent / "fixtures" / "xcodebuild"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_parse_errors_extracts_compile_diagnostics_with_location():
    errors = parse_errors(_load("compile_missing_import.txt"))
    assert len(errors) == 2
    first = errors[0]
    assert first.file.endswith("NetworkServiceTests.swift")
    assert first.line == 3
    assert first.column == 8
    assert first.severity == "error"
    assert "no such module" in first.message.lower()
    assert first.category == "missing_import"

    second = errors[1]
    assert second.line == 18
    assert second.category == "missing_import"


def test_parse_errors_categorizes_mock_and_type_mismatch():
    errors = parse_errors(_load("compile_mock_mismatch.txt"))
    cats = {e.category for e in errors}
    assert "mock_mismatch" in cats
    assert "type_mismatch" in cats


def test_parse_errors_extracts_xctest_failures():
    errors = parse_errors(_load("test_failure.txt"))
    failures = [e for e in errors if e.test_name]
    assert failures, "expected at least one XCTest failure"
    assert failures[0].test_name == "test_fetchUser_validId_returnsUser"
    assert "XCTAssertEqual failed" in failures[0].message


def test_parse_errors_flags_runtime_crash():
    errors = parse_errors(_load("runtime_crash.txt"))
    crashes = [e for e in errors if e.category == "runtime_crash"]
    assert crashes, "expected runtime crash markers"
    messages = {c.message for c in crashes}
    assert "EXC_BAD_INSTRUCTION" in messages


def test_parse_errors_returns_empty_on_clean_build():
    assert parse_errors(_load("compile_success.txt")) == []
    assert parse_errors(_load("test_success.txt")) == []


def test_categorize_errors_groups_by_category():
    errors = parse_errors(_load("compile_mock_mismatch.txt"))
    grouped = categorize_errors(errors)
    assert set(grouped.keys()) <= {
        "mock_mismatch",
        "type_mismatch",
        "missing_import",
        "access_control",
        "async_issue",
        "runtime_crash",
        "test_failure",
        "other",
    }
    assert sum(len(v) for v in grouped.values()) == len(errors)
