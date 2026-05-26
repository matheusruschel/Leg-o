from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lego.models import GeneratedTest
from lego.validator import FeedbackLoopConfig, validate_and_fix


FIXTURES = Path(__file__).parent / "fixtures" / "xcodebuild"
GENERATOR_FIXTURES = Path(__file__).parent / "fixtures" / "generator"
SAMPLE_TEST = (GENERATOR_FIXTURES / "SampleNetworkServiceTests.swift").read_text()


def _generated() -> GeneratedTest:
    return GeneratedTest(
        file_content=SAMPLE_TEST,
        target_class="NetworkService",
        target_methods=["fetchUser"],
    )


def _load(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_validate_and_fix_skipped_when_no_xcodeproj(tmp_path: Path):
    config = FeedbackLoopConfig(xcodeproj=None, scheme=None, test_target_dir=tmp_path)
    result = validate_and_fix(_generated(), "// src", config, claude_client=MagicMock())
    assert result.skipped is True
    assert result.compiled is False
    assert result.passed is False


def test_validate_and_fix_passes_on_first_try(tmp_path: Path):
    config = FeedbackLoopConfig(
        xcodeproj=tmp_path / "App.xcodeproj",
        scheme="App",
        test_target_dir=tmp_path,
        max_retries=3,
    )
    compile_fn = MagicMock(return_value=(True, _load("compile_success.txt")))
    run_fn = MagicMock(return_value=(True, _load("test_success.txt")))

    result = validate_and_fix(
        _generated(), "// src", config,
        claude_client=MagicMock(),
        compile_fn=compile_fn, run_fn=run_fn,
    )

    assert result.compiled is True
    assert result.passed is True
    assert result.retry_count == 0
    assert [it.step for it in result.iterations] == ["compile", "test"]
    compile_fn.assert_called_once()
    run_fn.assert_called_once()


def test_validate_and_fix_compiles_after_one_fix_then_test_fixed(tmp_path: Path):
    config = FeedbackLoopConfig(
        xcodeproj=tmp_path / "App.xcodeproj",
        scheme="App",
        test_target_dir=tmp_path,
        max_retries=3,
    )

    compile_outputs = [
        (False, _load("compile_missing_import.txt")),  # first attempt fails compile
        (True, _load("compile_success.txt")),           # after fix, compile passes
        (True, _load("compile_success.txt")),           # after second fix, still passes
    ]
    test_outputs = [
        (False, _load("test_failure.txt")),  # first test run fails
        (True, _load("test_success.txt")),    # after fix, test passes
    ]
    compile_fn = MagicMock(side_effect=compile_outputs)
    run_fn = MagicMock(side_effect=test_outputs)

    claude = MagicMock()
    claude.call.return_value = SAMPLE_TEST  # the "fixed" file returned by Claude

    result = validate_and_fix(
        _generated(), "// src content", config,
        claude_client=claude,
        compile_fn=compile_fn, run_fn=run_fn,
    )

    assert result.compiled is True
    assert result.passed is True
    # Compile called 3x (initial, after compile fix, after test fix); run called 2x
    assert compile_fn.call_count == 3
    assert run_fn.call_count == 2
    # Two fix calls to Claude (one for compile error, one for test failure)
    assert claude.call.call_count == 2
    assert result.retry_count == 2
    steps = [it.step for it in result.iterations]
    assert steps == ["compile", "compile", "test", "compile", "test"]


def test_validate_and_fix_passes_workspace_to_xcodebuild(tmp_path: Path):
    config = FeedbackLoopConfig(
        xcworkspace=tmp_path / "App.xcworkspace",
        scheme="App",
        test_target_dir=tmp_path,
        max_retries=1,
    )
    compile_fn = MagicMock(return_value=(True, "** BUILD SUCCEEDED **"))
    run_fn = MagicMock(return_value=(True, "** TEST SUCCEEDED **"))

    validate_and_fix(
        _generated(), "// src", config,
        claude_client=MagicMock(),
        compile_fn=compile_fn, run_fn=run_fn,
    )

    # compile_fn should have been called with xcworkspace kwarg set, xcodeproj None
    kwargs = compile_fn.call_args.kwargs
    assert kwargs.get("xcworkspace") == tmp_path / "App.xcworkspace"
    args = compile_fn.call_args.args
    assert args[1] is None  # xcodeproj positional arg


def test_validate_and_fix_gives_up_after_max_retries(tmp_path: Path):
    config = FeedbackLoopConfig(
        xcodeproj=tmp_path / "App.xcodeproj",
        scheme="App",
        test_target_dir=tmp_path,
        max_retries=2,
    )
    compile_fn = MagicMock(return_value=(False, _load("compile_missing_import.txt")))
    run_fn = MagicMock()
    claude = MagicMock()
    claude.call.return_value = SAMPLE_TEST

    result = validate_and_fix(
        _generated(), "// src", config,
        claude_client=claude,
        compile_fn=compile_fn, run_fn=run_fn,
    )

    assert result.compiled is False
    assert result.passed is False
    assert compile_fn.call_count == 3  # initial + 2 retries
    run_fn.assert_not_called()
    assert result.retry_count == 2
