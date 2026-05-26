from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lego.xcode_project import XcodeProjectError, add_files_to_target


def _fake_project(target_names: list[str], add_returns: object = ["ok"]):
    project = MagicMock()
    project.objects.get_targets.return_value = [MagicMock(name=n) for n in target_names]
    # MagicMock(name=n) sets the mock's name attribute via the constructor — but we want
    # the attribute to be a real string. Patch it explicitly.
    for mock, name in zip(project.objects.get_targets.return_value, target_names):
        mock.name = name
    project.add_file.return_value = add_returns
    return project


def _make_pbxproj(tmp_path: Path) -> Path:
    xcodeproj = tmp_path / "App.xcodeproj"
    xcodeproj.mkdir()
    (xcodeproj / "project.pbxproj").write_text("// fake")
    return xcodeproj


def test_add_files_to_target_calls_add_file_and_saves(tmp_path: Path):
    xcodeproj = _make_pbxproj(tmp_path)
    test_file = tmp_path / "FooTests.swift"
    test_file.write_text("import XCTest")

    project = _fake_project(["App", "AppTests"])
    loader = MagicMock(return_value=project)

    added = add_files_to_target(xcodeproj, "AppTests", [test_file], project_loader=loader)

    assert added == [test_file]
    project.add_file.assert_called_once_with(str(test_file), target_name="AppTests", force=False)
    project.save.assert_called_once()


def test_add_files_to_target_skips_nonexistent_files(tmp_path: Path):
    xcodeproj = _make_pbxproj(tmp_path)
    missing = tmp_path / "Missing.swift"
    project = _fake_project(["App", "AppTests"])

    added = add_files_to_target(
        xcodeproj, "AppTests", [missing], project_loader=MagicMock(return_value=project),
    )
    assert added == []
    project.add_file.assert_not_called()
    project.save.assert_not_called()


def test_add_files_to_target_raises_when_target_missing(tmp_path: Path):
    xcodeproj = _make_pbxproj(tmp_path)
    test_file = tmp_path / "FooTests.swift"
    test_file.write_text("// x")
    project = _fake_project(["App"])  # no AppTests target

    with pytest.raises(XcodeProjectError):
        add_files_to_target(
            xcodeproj, "AppTests", [test_file],
            project_loader=MagicMock(return_value=project),
        )


def test_add_files_to_target_raises_when_pbxproj_missing(tmp_path: Path):
    xcodeproj = tmp_path / "App.xcodeproj"
    xcodeproj.mkdir()
    with pytest.raises(XcodeProjectError):
        add_files_to_target(
            xcodeproj, "AppTests", [],
            project_loader=MagicMock(),
        )


def test_add_file_returning_falsy_is_treated_as_already_present(tmp_path: Path):
    xcodeproj = _make_pbxproj(tmp_path)
    test_file = tmp_path / "FooTests.swift"
    test_file.write_text("// x")

    project = _fake_project(["App", "AppTests"], add_returns=[])  # already present
    added = add_files_to_target(
        xcodeproj, "AppTests", [test_file],
        project_loader=MagicMock(return_value=project),
    )
    assert added == []
    project.save.assert_not_called()
