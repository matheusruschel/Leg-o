from __future__ import annotations

import json
from pathlib import Path

import pytest

from lego.scanner import file_discovery, swift_scanner


def _scan(path: Path) -> list[dict]:
    files = file_discovery.discover_files(path)
    out: list[dict] = []
    for sf in files:
        for cls in swift_scanner.scan_file(sf):
            out.append(cls.model_dump(mode="json"))
    return out


def _scan_one(fixtures_dir: Path, name: str) -> list[dict]:
    return _scan(fixtures_dir / name)


# ---------------------------------------------------------------------------
# file_discovery
# ---------------------------------------------------------------------------


def test_discover_finds_all_swift_fixtures(fixtures_dir):
    files = file_discovery.discover_files(fixtures_dir)
    names = sorted(f.path.name for f in files)
    assert names == [
        "network_service.swift",
        "protocol_example.swift",
        "simple_model.swift",
        "singleton_heavy.swift",
        "view_controller.swift",
    ]
    assert all(f.language == "swift" for f in files)


def test_discover_skips_test_files(tmp_path):
    (tmp_path / "Foo.swift").write_text("class Foo {}")
    (tmp_path / "FooTests.swift").write_text("class FooTests {}")
    (tmp_path / "Foo.generated.swift").write_text("class Gen {}")
    skip = tmp_path / "Pods" / "Lib"
    skip.mkdir(parents=True)
    (skip / "Vendored.swift").write_text("class Vendored {}")
    found = sorted(f.path.name for f in file_discovery.discover_files(tmp_path))
    assert found == ["Foo.swift"]


def test_discover_objc_only_when_flag_set(tmp_path):
    (tmp_path / "Foo.swift").write_text("class Foo {}")
    (tmp_path / "Bar.m").write_text("@implementation Bar @end")
    (tmp_path / "Bar.h").write_text("@interface Bar @end")
    swift_only = file_discovery.discover_files(tmp_path)
    assert {f.path.name for f in swift_only} == {"Foo.swift"}
    both = file_discovery.discover_files(tmp_path, include_objc=True)
    assert {f.path.name for f in both} == {"Foo.swift", "Bar.m", "Bar.h"}


# ---------------------------------------------------------------------------
# swift_scanner — simple_model
# ---------------------------------------------------------------------------


def test_scan_simple_model(fixtures_dir):
    classes = _scan_one(fixtures_dir, "simple_model.swift")
    assert len(classes) == 1
    user = classes[0]
    assert user["name"] == "User"
    assert user["kind"] == "struct"
    assert user["superclass"] is None
    assert user["imports"] == ["Foundation"]
    prop_names = [p["name"] for p in user["properties"]]
    assert prop_names == ["name", "email"]
    for p in user["properties"]:
        assert p["is_let"] is True
        assert p["type"] == "String"
        assert p["injection_style"] == "init"
    method_names = [m["name"] for m in user["methods"]]
    assert method_names == ["init", "isValidEmail", "displayName"]
    is_valid = next(m for m in user["methods"] if m["name"] == "isValidEmail")
    assert is_valid["return_type"] == "Bool"


# ---------------------------------------------------------------------------
# swift_scanner — protocol_example
# ---------------------------------------------------------------------------


def test_scan_protocol_example(fixtures_dir):
    classes = _scan_one(fixtures_dir, "protocol_example.swift")
    by_name = {c["name"]: c for c in classes}

    assert set(by_name) == {"PaymentGateway", "PaymentLogger", "PaymentProcessor", "PaymentError"}
    assert by_name["PaymentGateway"]["kind"] == "protocol"
    assert by_name["PaymentLogger"]["kind"] == "protocol"
    assert by_name["PaymentError"]["kind"] == "enum"
    # Error is a protocol, not a superclass.
    assert by_name["PaymentError"]["superclass"] is None
    assert "Error" in by_name["PaymentError"]["protocols"]

    proc = by_name["PaymentProcessor"]
    assert proc["kind"] == "class"
    assert proc["superclass"] is None
    prop_names = [p["name"] for p in proc["properties"]]
    assert prop_names == ["gateway", "logger", "maxAmount"]
    for p in proc["properties"]:
        assert p["is_let"] is True
        assert p["injection_style"] == "init"
    method_names = [m["name"] for m in proc["methods"]]
    assert "init" in method_names and "process" in method_names and "refund" in method_names
    process = next(m for m in proc["methods"] if m["name"] == "process")
    assert process["return_type"] == "String"
    deps = {d["type_name"] for d in proc["dependencies"]}
    assert {"PaymentGateway", "PaymentLogger"}.issubset(deps)


# ---------------------------------------------------------------------------
# swift_scanner — view_controller
# ---------------------------------------------------------------------------


def test_scan_view_controller(fixtures_dir):
    classes = _scan_one(fixtures_dir, "view_controller.swift")
    assert len(classes) == 1
    vc = classes[0]
    assert vc["name"] == "ProfileViewController"
    assert vc["superclass"] == "UIViewController"
    assert "UITableViewDataSource" in vc["protocols"]

    props = {p["name"]: p for p in vc["properties"]}
    # @IBOutlet attribute should not pollute the property's type field.
    assert props["tableView"]["type"] == "UITableView!"
    assert props["nameLabel"]["type"] == "UILabel!"
    assert props["networkService"]["type"] == "NetworkServiceProtocol"
    assert props["networkService"]["injection_style"] == "init"

    method_names = [m["name"] for m in vc["methods"]]
    assert "viewDidLoad" in method_names
    assert "loadUsers" in method_names
    assert method_names.count("tableView") == 2  # both delegate methods

    cached = next(m for m in vc["methods"] if m["name"] == "cachedUserName")
    assert cached["return_type"] == "String?"

    rows = next(
        m for m in vc["methods"]
        if m["name"] == "tableView"
        and any(p["label"] == "numberOfRowsInSection" for p in m["parameters"])
    )
    assert rows["return_type"] == "Int"
    # Wildcard external label `_` is preserved.
    assert rows["parameters"][0]["label"] == "_"
    assert rows["parameters"][0]["name"] == "tableView"

    deps = {d["type_name"] for d in vc["dependencies"]}
    assert "NetworkServiceProtocol" in deps
    # IBOutlet must not appear as a dependency type.
    assert "IBOutlet" not in deps


# ---------------------------------------------------------------------------
# swift_scanner — network_service
# ---------------------------------------------------------------------------


def test_scan_network_service(fixtures_dir):
    classes = _scan_one(fixtures_dir, "network_service.swift")
    by_name = {c["name"]: c for c in classes}
    assert by_name["NetworkServiceProtocol"]["kind"] == "protocol"
    svc = by_name["NetworkService"]
    assert svc["kind"] == "class"
    # NetworkServiceProtocol is a protocol, not a superclass.
    assert svc["superclass"] is None
    assert "NetworkServiceProtocol" in svc["protocols"]


# ---------------------------------------------------------------------------
# Golden snapshots — regenerate with LEGO_REGEN=1
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture", ["simple_model.swift", "protocol_example.swift", "network_service.swift"])
def test_golden_ast_snapshot(fixtures_dir, fixture):
    import os

    expected_path = fixtures_dir / "expected_ast" / fixture.replace(".swift", ".json")
    actual = _scan_one(fixtures_dir, fixture)
    # Normalize file paths so snapshots are portable.
    for entry in actual:
        entry["file_path"] = Path(entry["file_path"]).name

    if os.environ.get("LEGO_REGEN") == "1" or not expected_path.exists():
        expected_path.parent.mkdir(parents=True, exist_ok=True)
        expected_path.write_text(json.dumps(actual, indent=2) + "\n")

    expected = json.loads(expected_path.read_text())
    assert actual == expected
