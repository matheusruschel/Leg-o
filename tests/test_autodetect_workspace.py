from pathlib import Path

from lego.orchestrator import autodetect_workspace


def test_autodetect_workspace_returns_sibling_when_present(tmp_path: Path):
    proj = tmp_path / "App.xcodeproj"
    proj.mkdir()
    workspace = tmp_path / "App.xcworkspace"
    workspace.mkdir()
    assert autodetect_workspace(proj) == workspace


def test_autodetect_workspace_returns_none_when_absent(tmp_path: Path):
    proj = tmp_path / "App.xcodeproj"
    proj.mkdir()
    assert autodetect_workspace(proj) is None
