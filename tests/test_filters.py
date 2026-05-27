from __future__ import annotations

from pathlib import Path

from lego.filters import (
    covered_methods_in,
    filter_non_empty_methods,
    find_existing_test_files,
    is_data_holder,
    is_empty_method,
    is_ui_type,
)
from lego.models import ClassMetadata, MethodMetadata


def _class(name="Foo", **kwargs) -> ClassMetadata:
    base = dict(name=name, kind="class", file_path=Path(f"{name}.swift"))
    base.update(kwargs)
    return ClassMetadata(**base)


def _method(name="foo", body="", line_count=0) -> MethodMetadata:
    return MethodMetadata(name=name, body_text=body, line_count=line_count)


# ---- UI types ----

def test_is_ui_type_catches_uiviewcontroller_subclass():
    c = _class(superclass="UIViewController")
    assert is_ui_type(c) is not None


def test_is_ui_type_catches_custom_cell_subclass():
    c = _class(superclass="UITableViewCell")
    assert is_ui_type(c) is not None


def test_is_ui_type_catches_swiftui_view():
    c = _class(kind="struct", protocols=["View"])
    assert is_ui_type(c) is not None


def test_is_ui_type_passes_through_non_ui_class():
    c = _class(superclass="NSObject")
    assert is_ui_type(c) is None


# ---- Data holders ----

def test_is_data_holder_no_methods_struct():
    c = _class(name="DTO", kind="struct")
    assert is_data_holder(c) is not None


def test_is_data_holder_only_trivial_methods():
    c = _class(name="DTO", kind="struct", methods=[
        _method(name="==", body="lhs.id == rhs.id", line_count=1),
    ])
    assert is_data_holder(c) is not None


def test_is_data_holder_skips_class_with_logic():
    c = _class(name="Service", kind="struct", methods=[
        _method(name="run", body="if x { return 1 } else { return 0 }", line_count=4),
    ])
    assert is_data_holder(c) is None


def test_is_data_holder_only_runs_on_struct_or_enum():
    c = _class(name="Service", kind="class")
    assert is_data_holder(c) is None


# ---- Empty methods ----

def test_is_empty_method_true_for_empty_body():
    assert is_empty_method(_method(body="")) is True
    assert is_empty_method(_method(body="{ }")) is True
    assert is_empty_method(_method(body="{\n}")) is True


def test_is_empty_method_false_when_body_has_code():
    assert is_empty_method(_method(body="return 42")) is False


def test_filter_non_empty_methods_drops_empties():
    c = _class(methods=[
        _method(name="a", body=""),
        _method(name="b", body="return 1"),
        _method(name="c", body="{ }"),
    ])
    kept = filter_non_empty_methods(c)
    assert [m.name for m in kept] == ["b"]


# ---- Existing tests + covered methods ----

def test_find_existing_test_files(tmp_path: Path):
    (tmp_path / "FooTests.swift").write_text("// x")
    (tmp_path / "BarTests.m").write_text("// y")
    (tmp_path / "NotATest.swift").write_text("// z")
    (tmp_path / "Helpers" / "BazTests.swift").parent.mkdir()
    (tmp_path / "Helpers" / "BazTests.swift").write_text("// q")

    found = find_existing_test_files(tmp_path)
    assert set(found.keys()) == {"Foo", "Bar", "Baz"}


def test_covered_methods_in_extracts_swift_and_objc(tmp_path: Path):
    f = tmp_path / "FooTests.swift"
    f.write_text(
        "import XCTest\n"
        "class FooTests: XCTestCase {\n"
        "    func test_fetchUser_validId_returnsUser() {}\n"
        "    func testRunBlock() {}\n"
        "    - (void)test_save_failure_returnsError {}\n"
        "}\n"
    )
    names = covered_methods_in(f)
    # All normalized to lowerCamelCase first segment
    assert "fetchUser" in names
    assert "runBlock" in names
    assert "save" in names


def test_covered_methods_in_missing_file_returns_empty(tmp_path: Path):
    assert covered_methods_in(tmp_path / "missing.swift") == set()
