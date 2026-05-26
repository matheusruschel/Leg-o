from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from ..generator.test_generator import fix_tests, write_test_file
from ..llm.client import ClaudeClient
from ..models import GeneratedTest, ParsedError, ValidationIteration, ValidationResult
from . import error_parser

log = logging.getLogger(__name__)


@dataclass
class FeedbackLoopConfig:
    xcodeproj: Optional[Path] = None
    scheme: Optional[str] = None
    test_target_dir: Optional[Path] = None
    max_retries: int = 3


def validate_and_fix(
    generated_test: GeneratedTest,
    source_content: str,
    config: FeedbackLoopConfig,
    claude_client: ClaudeClient,
    compile_fn: Optional[Callable] = None,
    run_fn: Optional[Callable] = None,
) -> ValidationResult:
    if config.xcodeproj is None or config.scheme is None or config.test_target_dir is None:
        return ValidationResult(
            skipped=True,
            skipped_reason="no xcodeproj/scheme/test_target_dir provided; skipping validation",
        )

    # Lazy import to keep tests fast and avoid the macOS-only path at module load.
    if compile_fn is None or run_fn is None:
        from .xcodebuild import compile_test, run_test
        compile_fn = compile_fn or compile_test
        run_fn = run_fn or run_test

    result = ValidationResult()
    current_test = generated_test
    test_path = write_test_file(current_test, config.test_target_dir)

    for attempt in range(config.max_retries + 1):
        compiled, compile_output = compile_fn(
            test_path, config.xcodeproj, config.scheme,
        )
        result.iterations.append(
            ValidationIteration(
                step="compile",
                success=compiled,
                errors=error_parser.parse_errors(compile_output) if not compiled else [],
                raw_output=compile_output,
            )
        )

        if not compiled:
            if attempt >= config.max_retries:
                result.retry_count = attempt
                return result
            current_test = _attempt_fix(
                current_test, compile_output, source_content, claude_client
            )
            test_path = write_test_file(current_test, config.test_target_dir)
            continue

        result.compiled = True

        passed, test_output = run_fn(
            test_path, config.xcodeproj, config.scheme,
            test_class=f"{current_test.target_class}Tests",
        )
        result.iterations.append(
            ValidationIteration(
                step="test",
                success=passed,
                errors=error_parser.parse_errors(test_output) if not passed else [],
                raw_output=test_output,
            )
        )

        if passed:
            result.passed = True
            result.retry_count = attempt
            return result

        if attempt >= config.max_retries:
            result.retry_count = attempt
            return result

        current_test = _attempt_fix(
            current_test, test_output, source_content, claude_client
        )
        test_path = write_test_file(current_test, config.test_target_dir)

    result.retry_count = config.max_retries
    return result


def _attempt_fix(
    failing_test: GeneratedTest,
    error_output: str,
    source_content: str,
    claude_client: ClaudeClient,
) -> GeneratedTest:
    errors = error_parser.parse_errors(error_output)
    log.info("fix attempt: %d parsed errors", len(errors))
    return fix_tests(failing_test, error_output, source_content, claude_client)
