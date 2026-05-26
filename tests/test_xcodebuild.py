from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lego.validator.xcodebuild import compile_test, run_test


def _fake_runner(returncode: int = 0, stdout: str = "", stderr: str = ""):
    proc = MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)
    runner = MagicMock(return_value=proc)
    return runner


def test_compile_test_uses_workspace_flag_when_provided(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("lego.validator.xcodebuild.shutil.which", lambda _: "/usr/bin/xcodebuild")
    workspace = tmp_path / "App.xcworkspace"
    workspace.mkdir()
    runner = _fake_runner(returncode=0, stdout="** BUILD SUCCEEDED **")

    ok, _out = compile_test(
        test_file_path=tmp_path / "FooTests.swift",
        xcodeproj=None,
        scheme="App",
        runner=runner,
        xcworkspace=workspace,
    )
    assert ok is True
    cmd = runner.call_args.args[0]
    assert "-workspace" in cmd
    assert "-project" not in cmd
    assert str(workspace) in cmd


def test_compile_test_uses_project_flag_when_no_workspace(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("lego.validator.xcodebuild.shutil.which", lambda _: "/usr/bin/xcodebuild")
    proj = tmp_path / "App.xcodeproj"
    proj.mkdir()
    runner = _fake_runner(returncode=0)

    compile_test(
        test_file_path=tmp_path / "FooTests.swift",
        xcodeproj=proj, scheme="App", runner=runner,
    )
    cmd = runner.call_args.args[0]
    assert "-project" in cmd
    assert "-workspace" not in cmd


def test_workspace_takes_precedence_when_both_set(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("lego.validator.xcodebuild.shutil.which", lambda _: "/usr/bin/xcodebuild")
    proj = tmp_path / "App.xcodeproj"
    proj.mkdir()
    workspace = tmp_path / "App.xcworkspace"
    workspace.mkdir()
    runner = _fake_runner(returncode=0)

    run_test(
        test_file_path=tmp_path / "FooTests.swift",
        xcodeproj=proj, scheme="App", test_class="FooTests",
        runner=runner, xcworkspace=workspace,
    )
    cmd = runner.call_args.args[0]
    assert "-workspace" in cmd
    assert "-project" not in cmd


def test_missing_workspace_raises(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("lego.validator.xcodebuild.shutil.which", lambda _: "/usr/bin/xcodebuild")
    with pytest.raises(FileNotFoundError):
        compile_test(
            test_file_path=tmp_path / "FooTests.swift",
            xcodeproj=None, scheme="App",
            runner=_fake_runner(), xcworkspace=tmp_path / "missing.xcworkspace",
        )


def test_neither_xcodeproj_nor_workspace_raises(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("lego.validator.xcodebuild.shutil.which", lambda _: "/usr/bin/xcodebuild")
    with pytest.raises(ValueError):
        compile_test(
            test_file_path=tmp_path / "FooTests.swift",
            xcodeproj=None, scheme="App",
            runner=_fake_runner(),
        )
