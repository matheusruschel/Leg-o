from __future__ import annotations

import json
from unittest.mock import MagicMock

from lego.validator.xcodebuild import (
    autodetect_destination,
    list_available_ios_simulators,
)


def _simctl_runner(payload: dict, returncode: int = 0):
    proc = MagicMock(returncode=returncode, stdout=json.dumps(payload), stderr="")
    return MagicMock(return_value=proc)


def test_list_available_ios_simulators_filters_ios_runtimes(monkeypatch):
    monkeypatch.setattr("lego.validator.xcodebuild.shutil.which", lambda _: "/usr/bin/xcrun")
    runner = _simctl_runner({
        "devices": {
            "com.apple.CoreSimulator.SimRuntime.iOS-18-0": [
                {"name": "iPhone 16", "isAvailable": True, "udid": "a"},
                {"name": "iPad Pro", "isAvailable": True, "udid": "b"},
            ],
            "com.apple.CoreSimulator.SimRuntime.tvOS-18-0": [
                {"name": "Apple TV", "isAvailable": True, "udid": "c"},
            ],
            "com.apple.CoreSimulator.SimRuntime.iOS-17-0": [
                {"name": "iPhone 15", "isAvailable": False, "udid": "d"},
            ],
        }
    })
    devices = list_available_ios_simulators(runner=runner)
    names = {d["name"] for d in devices}
    # Only iOS, only available
    assert names == {"iPhone 16", "iPad Pro"}


def test_autodetect_prefers_iphone_16_when_present(monkeypatch):
    monkeypatch.setattr("lego.validator.xcodebuild.shutil.which", lambda _: "/usr/bin/xcrun")
    runner = _simctl_runner({
        "devices": {
            "iOS 18.0": [
                {"name": "iPhone 16", "isAvailable": True},
                {"name": "iPhone 15", "isAvailable": True},
            ],
        }
    })
    assert autodetect_destination(runner=runner) == "platform=iOS Simulator,name=iPhone 16"


def test_autodetect_falls_back_to_next_preferred(monkeypatch):
    monkeypatch.setattr("lego.validator.xcodebuild.shutil.which", lambda _: "/usr/bin/xcrun")
    runner = _simctl_runner({
        "devices": {
            "iOS 17.0": [
                {"name": "iPhone 15 Pro", "isAvailable": True},
            ],
        }
    })
    # iPhone 16 missing → iPhone 15 family wins (startswith match)
    assert autodetect_destination(runner=runner) == "platform=iOS Simulator,name=iPhone 15 Pro"


def test_autodetect_falls_back_to_first_iphone(monkeypatch):
    monkeypatch.setattr("lego.validator.xcodebuild.shutil.which", lambda _: "/usr/bin/xcrun")
    runner = _simctl_runner({
        "devices": {
            "iOS 16.0": [
                {"name": "iPhone SE (3rd generation)", "isAvailable": True},
            ],
        }
    })
    assert autodetect_destination(runner=runner) == "platform=iOS Simulator,name=iPhone SE (3rd generation)"


def test_autodetect_returns_none_when_no_simulators(monkeypatch):
    monkeypatch.setattr("lego.validator.xcodebuild.shutil.which", lambda _: "/usr/bin/xcrun")
    runner = _simctl_runner({"devices": {}})
    assert autodetect_destination(runner=runner) is None


def test_autodetect_returns_none_when_xcrun_missing(monkeypatch):
    monkeypatch.setattr("lego.validator.xcodebuild.shutil.which", lambda _: None)
    assert list_available_ios_simulators() == []
    assert autodetect_destination() is None
