from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lego.generator import build_context, generate_tests, write_test_file
from lego.generator.test_generator import InvalidTestOutput, fix_tests
from lego.models import (
    ClassMetadata,
    DependencyAssessment,
    DependencyInfo,
    MethodMetadata,
    ParameterMetadata,
    PropertyMetadata,
    SwiftFile,
    TestabilityResult,
)


FIXTURES = Path(__file__).parent / "fixtures"


def _swift(path: str, content: str) -> SwiftFile:
    return SwiftFile(path=Path(path), content=content, language="swift")


def _network_service_target() -> tuple[ClassMetadata, list[SwiftFile]]:
    target_content = (
        "import Foundation\n"
        "final class NetworkService {\n"
        "    private let session: URLSessionProtocol\n"
        "    init(session: URLSessionProtocol) { self.session = session }\n"
        "    func fetchUser(id: Int, completion: @escaping (Result<User, Error>) -> Void) {}\n"
        "}\n"
    )
    protocol_file = _swift(
        "URLSessionProtocol.swift",
        "protocol URLSessionProtocol {\n"
        "    func data(for request: URLRequest, completion: @escaping (Result<Data, Error>) -> Void)\n"
        "}\n",
    )
    user_model = _swift(
        "User.swift",
        "struct User: Codable { let id: Int; let name: String }\n",
    )
    unrelated = _swift(
        "Other.swift",
        "struct Other { let value: Int }\n",
    )
    target_file = _swift("NetworkService.swift", target_content)

    target = ClassMetadata(
        name="NetworkService",
        kind="class",
        file_path=Path("NetworkService.swift"),
        properties=[
            PropertyMetadata(
                name="session",
                type="URLSessionProtocol",
                is_let=True,
                injection_style="init",
                access_level="private",
            ),
        ],
        methods=[
            MethodMetadata(
                name="fetchUser",
                parameters=[
                    ParameterMetadata(name="id", type="Int"),
                    ParameterMetadata(
                        name="completion",
                        type="(Result<User, Error>) -> Void",
                    ),
                ],
            ),
        ],
        dependencies=[
            DependencyInfo(type_name="URLSessionProtocol", injection_style="init", is_protocol=True),
        ],
    )
    return target, [target_file, protocol_file, user_model, unrelated]


def test_build_context_includes_protocol_and_signature_types_skips_unrelated():
    target, files = _network_service_target()
    analysis = TestabilityResult(
        class_name="NetworkService",
        testable=True,
        testability_score=85,
        dependencies=[
            DependencyAssessment(
                type_name="URLSessionProtocol",
                mockable=True,
                mock_strategy="protocol",
                reason="already a protocol",
            )
        ],
        testable_methods=["fetchUser"],
    )

    bundle = build_context(target, files, analysis)

    assert bundle.target_class == "NetworkService"
    assert "URLSessionProtocol.swift" in bundle.related_contents
    assert "User.swift" in bundle.related_contents
    assert "Other.swift" not in bundle.related_contents
    assert "URLSessionProtocol" in bundle.analysis_summary


def test_build_context_respects_char_budget_and_prioritizes_protocols():
    target, files = _network_service_target()
    target_file = next(f for f in files if f.path == target.file_path)
    protocol_file = next(f for f in files if f.path.name == "URLSessionProtocol.swift")
    user_file = next(f for f in files if f.path.name == "User.swift")

    # Budget fits target + protocol with a sliver of slack, but not the User model on top.
    budget = len(target_file.content) + len(protocol_file.content) + (len(user_file.content) - 1)
    bundle = build_context(target, files, analysis=None, max_chars=budget)

    assert "URLSessionProtocol.swift" in bundle.related_contents
    assert "User.swift" not in bundle.related_contents


def test_build_context_raises_if_target_file_missing():
    target, files = _network_service_target()
    with pytest.raises(ValueError):
        build_context(target, files[1:], analysis=None)


def test_generate_tests_validates_and_returns_model():
    sample = (FIXTURES / "generator" / "SampleNetworkServiceTests.swift").read_text()
    client = MagicMock()
    client.call.return_value = sample

    target, files = _network_service_target()
    bundle = build_context(target, files, analysis=None)

    result = generate_tests(bundle, client, methods=["fetchUser"], module_name="MyApp")

    assert result.target_class == "NetworkService"
    assert result.target_methods == ["fetchUser"]
    assert "import XCTest" in result.file_content
    assert "class NetworkServiceTests" in result.file_content
    # Prompt should be built from the template + substitutions.
    sent_prompt = client.call.call_args.args[0][0]["content"]
    assert "NetworkService" in sent_prompt
    assert "MyApp" in sent_prompt
    assert "fetchUser" in sent_prompt


def test_generate_tests_strips_markdown_fences():
    sample = (FIXTURES / "generator" / "SampleNetworkServiceTests.swift").read_text()
    fenced = "Here you go:\n```swift\n" + sample + "\n```\n"
    client = MagicMock()
    client.call.return_value = fenced

    target, files = _network_service_target()
    bundle = build_context(target, files, analysis=None)

    result = generate_tests(bundle, client, methods=["fetchUser"], module_name="MyApp")
    assert "```" not in result.file_content
    assert result.file_content.startswith("import XCTest")


def test_generate_tests_rejects_non_xctest_output():
    client = MagicMock()
    client.call.return_value = "print(\"not a test file\")"

    target, files = _network_service_target()
    bundle = build_context(target, files, analysis=None)

    with pytest.raises(InvalidTestOutput):
        generate_tests(bundle, client, methods=["fetchUser"], module_name="MyApp")


def test_write_test_file_creates_named_file(tmp_path: Path):
    sample = (FIXTURES / "generator" / "SampleNetworkServiceTests.swift").read_text()
    client = MagicMock()
    client.call.return_value = sample

    target, files = _network_service_target()
    bundle = build_context(target, files, analysis=None)
    result = generate_tests(bundle, client, methods=["fetchUser"], module_name="MyApp")

    path = write_test_file(result, tmp_path)
    assert path == tmp_path / "NetworkServiceTests.swift"
    assert path.read_text() == sample
    assert result.output_path == path


def test_fix_tests_returns_repaired_code():
    sample = (FIXTURES / "generator" / "SampleNetworkServiceTests.swift").read_text()
    client = MagicMock()
    client.call.return_value = sample

    from lego.models import GeneratedTest

    failing = GeneratedTest(
        file_content="import XCTest\nclass NetworkServiceTests: XCTestCase { func testBroken() {} }",
        target_class="NetworkService",
        target_methods=["fetchUser"],
    )
    fixed = fix_tests(failing, "error: missing mock", "// source", client)
    assert "fetchUser" in fixed.file_content
    sent_prompt = client.call.call_args.args[0][0]["content"]
    assert "missing mock" in sent_prompt
