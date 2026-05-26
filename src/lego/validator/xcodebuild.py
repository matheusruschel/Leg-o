from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


DEFAULT_DESTINATION = "platform=iOS Simulator,name=iPhone 16"
DEFAULT_COMPILE_TIMEOUT = 120
DEFAULT_TEST_TIMEOUT = 180


class XcodebuildUnavailable(RuntimeError):
    """xcodebuild not available (e.g., not on macOS or Xcode not installed)."""


def _ensure_xcodebuild() -> str:
    path = shutil.which("xcodebuild")
    if not path:
        raise XcodebuildUnavailable(
            "xcodebuild not found on PATH; install Xcode or run on macOS"
        )
    return path


def _project_flags(xcodeproj: Path | None, xcworkspace: Path | None) -> list[str]:
    """Return the right -workspace/-project CLI flags. Workspace wins when both are set."""
    if xcworkspace is not None:
        target = Path(xcworkspace)
        if not target.exists():
            raise FileNotFoundError(f"xcode workspace not found: {target}")
        return ["-workspace", str(target)]
    if xcodeproj is not None:
        target = Path(xcodeproj)
        if not target.exists():
            raise FileNotFoundError(f"xcode project not found: {target}")
        return ["-project", str(target)]
    raise ValueError("must provide either xcodeproj or xcworkspace")


def compile_test(
    test_file_path: Path,
    xcodeproj: Path | None,
    scheme: str,
    destination: str = DEFAULT_DESTINATION,
    timeout: int = DEFAULT_COMPILE_TIMEOUT,
    runner=subprocess.run,
    xcworkspace: Path | None = None,
) -> tuple[bool, str]:
    """Run `xcodebuild build-for-testing`. Returns (success, combined_output)."""
    xcb = _ensure_xcodebuild()
    cmd = [xcb, "build-for-testing", *_project_flags(xcodeproj, xcworkspace),
           "-scheme", scheme, "-destination", destination]
    return _run(runner, cmd, timeout)


def run_test(
    test_file_path: Path,
    xcodeproj: Path | None,
    scheme: str,
    test_class: str,
    destination: str = DEFAULT_DESTINATION,
    timeout: int = DEFAULT_TEST_TIMEOUT,
    runner=subprocess.run,
    xcworkspace: Path | None = None,
) -> tuple[bool, str]:
    """Run `xcodebuild test` scoped to a single test class. Returns (success, output)."""
    xcb = _ensure_xcodebuild()
    cmd = [xcb, "test", *_project_flags(xcodeproj, xcworkspace),
           "-scheme", scheme, "-destination", destination,
           f"-only-testing:{scheme}Tests/{test_class}"]
    return _run(runner, cmd, timeout)


def _run(runner, cmd: list[str], timeout: int) -> tuple[bool, str]:
    try:
        proc = runner(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        return False, f"xcodebuild timed out after {timeout}s\n{e}"
    output = (proc.stdout or "") + (proc.stderr or "")
    if "Unable to find a destination" in output or "Unavailable" in output:
        return False, output + "\n(simulator destination may be unavailable)"
    return proc.returncode == 0, output
