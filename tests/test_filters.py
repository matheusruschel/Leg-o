from __future__ import annotations

from pathlib import Path

from lego.filters import (
    covered_methods_in,
    detect_test_framework,
    filter_non_empty_methods,
    filter_testable_methods,
    find_existing_test_files,
    is_builder_class,
    is_data_holder,
    is_empty_method,
    is_private_method,
    is_system_extension,
    is_trivial_wrapper,
    is_ui_type,
)
from lego.models import ClassMetadata, MethodMetadata


def _class(name="Foo", **kwargs) -> ClassMetadata:
    base = dict(name=name, kind="class", file_path=Path(f"{name}.swift"))
    base.update(kwargs)
    return ClassMetadata(**base)


def _method(name="foo", body="", line_count=0, access_level="internal", return_type=None) -> MethodMetadata:
    return MethodMetadata(
        name=name, body_text=body, line_count=line_count,
        access_level=access_level, return_type=return_type,
    )


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


def test_is_ui_type_catches_name_suffix_with_custom_base_class():
    """Catches views that extend a custom base class (e.g., LoadableFromNibView)."""
    c = _class(name="ASPToggleView", superclass="LoadableFromNibView")
    assert is_ui_type(c) is not None


def test_is_ui_type_catches_name_suffix_no_superclass():
    c = _class(name="NewsFeedCell")
    assert is_ui_type(c) is not None


def test_is_ui_type_skips_viewmodel_and_friends():
    for name in ("NewsViewModel", "NewsViewState", "NewsViewData",
                 "NewsViewProvider", "NewsViewBuilder"):
        c = _class(name=name)
        assert is_ui_type(c) is None, f"{name} should not be flagged as UI"


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


def test_is_system_extension_catches_uifont_extension():
    c = _class(name="UIFont", is_extension=True, extends="UIFont")
    assert is_system_extension(c) is not None


def test_is_system_extension_catches_nsstring_extension():
    c = _class(name="NSString", is_extension=True, extends="NSString")
    assert is_system_extension(c) is not None


def test_is_system_extension_catches_swiftui_font_extension():
    c = _class(name="Font", is_extension=True, extends="Font")
    assert is_system_extension(c) is not None


def test_is_system_extension_ignores_extensions_on_app_types():
    c = _class(name="WSLContent", is_extension=True, extends="WSLContent")
    assert is_system_extension(c) is None


def test_is_system_extension_ignores_non_extensions():
    c = _class(name="MyService", is_extension=False)
    assert is_system_extension(c) is None


# ---- Builder / DSL classes ----

def test_is_builder_class_catches_chainable_setter_class():
    """All methods return Self with a tiny body — classic DSL pattern."""
    methods = [
        _method(name=n, return_type="AttributedStringProxy", line_count=2)
        for n in ("font", "kern", "tracking", "foregroundColor", "backgroundColor")
    ]
    c = _class(name="AttributedStringProxy", methods=methods)
    assert is_builder_class(c) is not None


def test_is_builder_class_ignores_mixed_class():
    """Half builders, half real methods — not a DSL."""
    methods = [
        _method(name="font", return_type="Proxy", line_count=2),
        _method(name="render", return_type="String", line_count=15),
        _method(name="validate", return_type="Bool", line_count=10),
    ]
    c = _class(name="Proxy", methods=methods)
    assert is_builder_class(c) is None


def test_is_builder_class_ignores_few_methods():
    c = _class(name="Foo", methods=[
        _method(name="x", return_type="Foo", line_count=1),
        _method(name="y", return_type="Foo", line_count=1),
    ])
    assert is_builder_class(c) is None  # only 2 methods, below threshold


# ---- Trivial wrappers ----

def test_is_trivial_wrapper_catches_delegate_forward():
    assert is_trivial_wrapper(_method(body="{ delegate?.foo(arg) }")) is True


def test_is_trivial_wrapper_catches_passthrough_call():
    assert is_trivial_wrapper(_method(body="{ self.something.foo() }")) is True


def test_is_trivial_wrapper_catches_return_dot_access():
    assert is_trivial_wrapper(_method(body="{ return self.x.y }")) is True


def test_is_trivial_wrapper_catches_return_init():
    assert is_trivial_wrapper(_method(body="{ return MyType(value: 1) }")) is True


def test_is_trivial_wrapper_passes_real_logic():
    assert is_trivial_wrapper(_method(body="{ if x > 0 { return 1 } else { return 0 } }")) is False
    assert is_trivial_wrapper(_method(body="{ let y = x * 2; return y + offset }")) is False


def test_is_trivial_wrapper_passes_empty():
    """Empty bodies are handled by is_empty_method, not this filter."""
    assert is_trivial_wrapper(_method(body="")) is False


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


def test_is_private_method_true_for_private_and_fileprivate():
    assert is_private_method(_method(access_level="private")) is True
    assert is_private_method(_method(access_level="fileprivate")) is True


def test_is_private_method_false_for_internal_public_open():
    assert is_private_method(_method(access_level="internal")) is False
    assert is_private_method(_method(access_level="public")) is False
    assert is_private_method(_method(access_level="open")) is False


def test_filter_testable_methods_drops_empty_and_private():
    c = _class(methods=[
        _method(name="pub", body="return 1", line_count=1, access_level="public"),
        _method(name="priv", body="return 2", line_count=1, access_level="private"),
        _method(name="empty", body="", access_level="internal"),
        _method(name="internal", body="if x {}", line_count=2, access_level="internal"),
    ])
    kept = filter_testable_methods(c)
    assert [m.name for m in kept] == ["pub", "internal"]


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
    # Legacy mode: returns full normalized identifiers (no prefix splitting).
    # Callers normally pass candidate_methods for proper prefix-matching.
    assert "fetchUser_validId_returnsUser" in names
    assert "runBlock" in names
    assert "save_failure_returnsError" in names


def test_covered_methods_in_missing_file_returns_empty(tmp_path: Path):
    assert covered_methods_in(tmp_path / "missing.swift") == set()


def test_covered_methods_in_with_candidates_matches_camel_case_with_scenario(tmp_path: Path):
    """testIsShortReturnAfterFiveMinutes should cover isShortReturn."""
    f = tmp_path / "FooTests.swift"
    f.write_text(
        "import XCTest\n"
        "class FooTests: XCTestCase {\n"
        "    func testIsShortReturnAfterFiveMinutes() {}\n"
        "}\n"
    )
    covered = covered_methods_in(f, candidate_methods=["isShortReturn", "saveSession"])
    assert covered == {"isShortReturn"}


def test_covered_methods_in_with_candidates_prefers_longest_match(tmp_path: Path):
    """If both 'save' and 'saveSession' are candidates, testSaveSessionWorks should cover saveSession."""
    f = tmp_path / "FooTests.swift"
    f.write_text(
        "class FooTests: XCTestCase {\n"
        "    func testSaveSession_returnsTrue() {}\n"
        "}\n"
    )
    covered = covered_methods_in(f, candidate_methods=["save", "saveSession"])
    assert covered == {"saveSession"}


def test_covered_methods_in_with_candidates_underscore_form(tmp_path: Path):
    f = tmp_path / "FooTests.swift"
    f.write_text(
        "class FooTests: XCTestCase {\n"
        "    func test_isShortReturn_expired_returnsTrue() {}\n"
        "    func test_saveSession_works() {}\n"
        "}\n"
    )
    covered = covered_methods_in(
        f, candidate_methods=["isShortReturn", "saveSession", "clearSession"],
    )
    assert covered == {"isShortReturn", "saveSession"}


def test_covered_methods_in_swift_testing_with_candidates(tmp_path: Path):
    """Swift Testing files: @Test func methodName_scenario_result() — no test_ prefix."""
    f = tmp_path / "LiveStreamResumeServiceTests.swift"
    f.write_text(
        "import Testing\n"
        "import Foundation\n"
        "@testable import App\n"
        "\n"
        "@Suite(\"LiveStreamResumeService\")\n"
        "struct LiveStreamResumeServiceTests {\n"
        "    @Test(\"saveSession persists\")\n"
        "    func saveSession_storesEventId() {}\n"
        "\n"
        "    @Test(\"isShortReturn with no session\")\n"
        "    func isShortReturn_withNoStoredSession_returnsFalse() {}\n"
        "\n"
        "    @Test func isShortReturn_atExactThreshold_returnsTrue() {}\n"
        "}\n"
    )
    covered = covered_methods_in(
        f, candidate_methods=["isShortReturn", "saveSession", "clearSession", "storedEventId"],
    )
    assert covered == {"isShortReturn", "saveSession"}


def test_detect_test_framework_swift_testing(tmp_path: Path):
    (tmp_path / "AlphaTests.swift").write_text("import Testing\n@Suite struct A {}\n")
    (tmp_path / "BetaTests.swift").write_text("import Testing\n@Suite struct B {}\n")
    assert detect_test_framework(tmp_path) == "swift_testing"


def test_detect_test_framework_xctest(tmp_path: Path):
    (tmp_path / "AlphaTests.swift").write_text("import XCTest\nclass A: XCTestCase {}\n")
    assert detect_test_framework(tmp_path) == "xctest"


def test_detect_test_framework_defaults_to_xctest_when_empty(tmp_path: Path):
    assert detect_test_framework(tmp_path) == "xctest"


def test_detect_test_framework_majority_wins(tmp_path: Path):
    (tmp_path / "AlphaTests.swift").write_text("import XCTest\nclass A: XCTestCase {}\n")
    (tmp_path / "BetaTests.swift").write_text("import Testing\n@Suite struct B {}\n")
    (tmp_path / "GammaTests.swift").write_text("import Testing\n@Suite struct G {}\n")
    assert detect_test_framework(tmp_path) == "swift_testing"


def test_covered_methods_in_with_candidates_does_not_overmatch(tmp_path: Path):
    """testValidate covering a method named 'val' shouldn't false-positive."""
    f = tmp_path / "FooTests.swift"
    f.write_text(
        "class FooTests: XCTestCase {\n"
        "    func testValidate_happyPath() {}\n"
        "}\n"
    )
    # 'val' starts validate but with lowercase next char — should NOT count as covering 'val'.
    covered = covered_methods_in(f, candidate_methods=["val"])
    assert covered == set()
