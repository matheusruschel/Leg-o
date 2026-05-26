from __future__ import annotations

import re

from ..models import ParsedError


# Compiler diagnostics: "/path/File.swift:12:5: error: cannot find type 'Foo' in scope"
_DIAG_RE = re.compile(
    r"^(?P<file>[^\s:][^:]*?):(?P<line>\d+):(?P<col>\d+):\s+"
    r"(?P<severity>error|warning|fatal error):\s+(?P<msg>.+)$",
    re.MULTILINE,
)

# Linker / Undefined symbol: "  \"_OBJC_CLASS_$_Foo\", referenced from:"
_LINKER_RE = re.compile(r'Undefined symbol(?: for architecture [\w_]+)?:\s+"([^"]+)"')

# XCTest failures: "/path/FileTests.swift:42: error: -[ModuleTests.FooTests testThing] : XCTAssertEqual failed: ..."
_XCTEST_FAIL_RE = re.compile(
    r"^(?P<file>[^\s:][^:]*?):(?P<line>\d+):\s+error:\s+"
    r"-\[[\w\.]+\s+(?P<test>\w+)\]\s*:\s*(?P<msg>.+)$",
    re.MULTILINE,
)

# Runtime crash markers
_CRASH_SIGNALS = ("EXC_BAD_ACCESS", "EXC_BAD_INSTRUCTION", "SIGABRT", "SIGSEGV",
                  "Fatal error: Unexpectedly found nil")


def parse_errors(output: str) -> list[ParsedError]:
    errors: list[ParsedError] = []

    for m in _DIAG_RE.finditer(output):
        if m.group("severity") == "warning":
            continue
        errors.append(
            ParsedError(
                file=m.group("file"),
                line=int(m.group("line")),
                column=int(m.group("col")),
                message=m.group("msg").strip(),
                severity=m.group("severity"),
                category=_classify_diag(m.group("msg")),
            )
        )

    for m in _LINKER_RE.finditer(output):
        errors.append(
            ParsedError(
                symbol=m.group(1),
                message=f"Undefined symbol: {m.group(1)}",
                category="missing_import",
            )
        )

    for m in _XCTEST_FAIL_RE.finditer(output):
        errors.append(
            ParsedError(
                file=m.group("file"),
                line=int(m.group("line")),
                test_name=m.group("test"),
                message=m.group("msg").strip(),
                category=_classify_test_failure(m.group("msg")),
            )
        )

    for signal in _CRASH_SIGNALS:
        if signal in output:
            errors.append(
                ParsedError(
                    category="runtime_crash",
                    message=signal,
                )
            )

    return errors


def categorize_errors(errors: list[ParsedError]) -> dict[str, list[ParsedError]]:
    grouped: dict[str, list[ParsedError]] = {}
    for err in errors:
        grouped.setdefault(err.category, []).append(err)
    return grouped


def _classify_diag(msg: str) -> str:
    m = msg.lower()
    if "no such module" in m or "cannot find" in m and "in scope" in m:
        return "missing_import"
    if "is inaccessible due to" in m or "is not accessible" in m or "private" in m and "access" in m:
        return "access_control"
    if (
        "does not conform to protocol" in m
        or "type does not conform" in m
        or "candidate has non-matching type" in m
    ):
        return "mock_mismatch"
    if (
        "cannot convert value of type" in m
        or "cannot assign value of type" in m
        or "cannot subscript a value of type" in m
        or "is not convertible to" in m
    ):
        return "type_mismatch"
    if "expectation" in m or "async" in m or "await" in m:
        return "async_issue"
    return "other"


def _classify_test_failure(msg: str) -> str:
    m = msg.lower()
    if "expectation" in m or "wait" in m and "timeout" in m:
        return "async_issue"
    if "xctassertequal failed" in m or "xctassert" in m:
        return "test_failure"
    return "test_failure"
