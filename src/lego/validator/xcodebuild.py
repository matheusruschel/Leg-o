from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


DEFAULT_DESTINATION = "platform=iOS Simulator,name=iPhone 16"
DEFAULT_COMPILE_TIMEOUT = 120
DEFAULT_TEST_TIMEOUT = 180
_PREFERRED_DEVICE_NAMES = ("iPhone 16", "iPhone 15", "iPhone 14", "iPhone")


class XcodebuildUnavailable(RuntimeError):
    """xcodebuild not available (e.g., not on macOS or Xcode not installed)."""


def _ensure_xcodebuild() -> str:
    path = shutil.which("xcodebuild")
    if not path:
        raise XcodebuildUnavailable(
            "xcodebuild not found on PATH; install Xcode or run on macOS"
        )
    return path


def list_available_ios_simulators(runner=subprocess.run) -> list[dict]:
    """Return a list of available iOS simulator device dicts from `xcrun simctl list`.

    Each dict has at least 'name' and 'runtime' (the iOS version), plus simctl's own keys
    like 'isAvailable' and 'udid'. Returns [] on any failure — caller should handle.
    """
    xcrun = shutil.which("xcrun")
    if not xcrun:
        return []
    try:
        proc = runner([xcrun, "simctl", "list", "devices", "available", "--json"],
                      capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError):
        return []
    if proc.returncode != 0:
        return []
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return []

    devices: list[dict] = []
    for runtime_key, entries in (data.get("devices") or {}).items():
        if "iOS" not in runtime_key:
            continue
        for entry in entries:
            if entry.get("isAvailable", True):
                devices.append({**entry, "runtime": runtime_key})
    return devices


def autodetect_destination(runner=subprocess.run) -> str | None:
    """Pick a sensible iOS simulator destination string from installed devices.

    Returns DEFAULT_DESTINATION's device if available, else the first device
    matching a preferred name (iPhone 16 → 15 → 14 → any iPhone), else None.
    """
    devices = list_available_ios_simulators(runner=runner)
    if not devices:
        return None
    names = {d["name"] for d in devices if d.get("name")}
    for preferred in _PREFERRED_DEVICE_NAMES:
        match = next((n for n in names if n.startswith(preferred)), None)
        if match:
            return f"platform=iOS Simulator,name={match}"
    # Fallback: just use whatever's first.
    first = devices[0].get("name")
    return f"platform=iOS Simulator,name={first}" if first else None


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
