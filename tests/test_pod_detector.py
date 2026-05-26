from __future__ import annotations

from pathlib import Path

from lego.models import ClassMetadata
from lego.pod_detector import (
    classes_skipped_by_pod_import,
    find_podfile_lock,
    normalize_import,
    parse_pod_modules,
)


SAMPLE_LOCK = """\
PODS:
  - AFNetworking (3.2.1):
    - AFNetworking/NSURLSession (= 3.2.1)
  - AFNetworking/NSURLSession (3.2.1)
  - Alamofire (5.10.0)
  - AppAuth (1.7.5):
    - AppAuth/Core (= 1.7.5)

DEPENDENCIES:
  - AFNetworking
  - Alamofire
"""


def _make_class(name: str, imports: list[str]) -> ClassMetadata:
    return ClassMetadata(
        name=name,
        kind="class",
        file_path=Path(f"{name}.swift"),
        imports=imports,
    )


def test_parse_pod_modules_extracts_top_level_pods_only(tmp_path: Path):
    lock = tmp_path / "Podfile.lock"
    lock.write_text(SAMPLE_LOCK)
    pods = parse_pod_modules(lock)
    assert pods == {"AFNetworking", "Alamofire", "AppAuth"}


def test_find_podfile_lock_walks_upward(tmp_path: Path):
    nested = tmp_path / "WSL" / "Sources" / "Feature"
    nested.mkdir(parents=True)
    lock = tmp_path / "WSL" / "Podfile.lock"
    lock.write_text(SAMPLE_LOCK)

    found = find_podfile_lock(nested)
    assert found == lock


def test_find_podfile_lock_returns_none_when_missing(tmp_path: Path):
    assert find_podfile_lock(tmp_path) is None


def test_classes_skipped_by_pod_import_partitions_correctly():
    classes = [
        _make_class("UsesAlamofire", ["Foundation", "Alamofire"]),
        _make_class("UsesNothingExternal", ["Foundation"]),
        _make_class("UsesAppAuth", ["AppAuth"]),
    ]
    kept, skipped = classes_skipped_by_pod_import(
        classes, pod_modules={"Alamofire", "AppAuth"}
    )
    assert [c.name for c in kept] == ["UsesNothingExternal"]
    assert {c.name for c, _ in skipped} == {"UsesAlamofire", "UsesAppAuth"}
    # matched sets are populated
    matched_by_name = {c.name: matched for c, matched in skipped}
    assert matched_by_name["UsesAlamofire"] == {"Alamofire"}


def test_normalize_import_handles_swift_and_objc_forms():
    # Swift: bare module name (passes through)
    assert normalize_import("Alamofire") == "Alamofire"
    # ObjC angle imports (umbrella header)
    assert normalize_import("#import <Alamofire/Alamofire.h>") == "Alamofire"
    assert normalize_import("#import <FBSDKCoreKit/FBSDKAppEvents.h>") == "FBSDKCoreKit"
    # ObjC angle include
    assert normalize_import("#include <FooKit/Bar.h>") == "FooKit"
    # ObjC @import module syntax
    assert normalize_import("@import Alamofire;") == "Alamofire"
    # ObjC quoted-form import is file-local, not a module
    assert normalize_import('#import "MyLocalHeader.h"') is None
    # Empty / garbage
    assert normalize_import("") is None


def test_classes_skipped_by_pod_import_works_for_objc_import_directives():
    objc_class = ClassMetadata(
        name="LegacyService",
        kind="class",
        file_path=Path("LegacyService.m"),
        imports=[
            '#import "LegacyService.h"',
            "#import <Alamofire/Alamofire.h>",
            "#import <Foundation/Foundation.h>",
        ],
    )
    swift_class = ClassMetadata(
        name="ModernService",
        kind="class",
        file_path=Path("ModernService.swift"),
        imports=["Foundation", "Alamofire"],
    )
    clean_class = ClassMetadata(
        name="Clean",
        kind="class",
        file_path=Path("Clean.swift"),
        imports=["Foundation"],
    )
    kept, skipped = classes_skipped_by_pod_import(
        [objc_class, swift_class, clean_class],
        pod_modules={"Alamofire"},
    )
    assert [c.name for c in kept] == ["Clean"]
    assert {c.name for c, _ in skipped} == {"LegacyService", "ModernService"}


def test_classes_skipped_by_pod_import_noop_when_no_pods():
    classes = [_make_class("Foo", ["Foundation"])]
    kept, skipped = classes_skipped_by_pod_import(classes, pod_modules=set())
    assert kept == classes
    assert skipped == []
