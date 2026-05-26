from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field


Language = Literal["swift", "objc"]
AccessLevel = Literal["open", "public", "internal", "fileprivate", "private", "package"]
InjectionStyle = Literal["init", "property", "none"]
KindLiteral = Literal["class", "struct", "enum", "protocol", "extension", "actor"]


class SwiftFile(BaseModel):
    path: Path
    content: str
    language: Language


class PropertyMetadata(BaseModel):
    name: str
    type: Optional[str] = None
    is_optional: bool = False
    is_let: bool = False
    access_level: AccessLevel = "internal"
    is_static: bool = False
    injection_style: InjectionStyle = "none"
    has_default: bool = False
    is_computed: bool = False


class ParameterMetadata(BaseModel):
    label: Optional[str] = None
    name: str
    type: Optional[str] = None
    default_value: Optional[str] = None


class MethodMetadata(BaseModel):
    name: str
    parameters: list[ParameterMetadata] = Field(default_factory=list)
    return_type: Optional[str] = None
    access_level: AccessLevel = "internal"
    is_static: bool = False
    is_async: bool = False
    throws: bool = False
    line_count: int = 0
    body_text: str = ""


class DependencyInfo(BaseModel):
    type_name: str
    injection_style: InjectionStyle
    is_protocol: bool = False
    is_singleton: bool = False
    is_system_framework: bool = False


class ClassMetadata(BaseModel):
    name: str
    kind: KindLiteral
    file_path: Path
    superclass: Optional[str] = None
    protocols: list[str] = Field(default_factory=list)
    properties: list[PropertyMetadata] = Field(default_factory=list)
    methods: list[MethodMetadata] = Field(default_factory=list)
    imports: list[str] = Field(default_factory=list)
    dependencies: list[DependencyInfo] = Field(default_factory=list)
    is_extension: bool = False
    extends: Optional[str] = None
    line_start: int = 0
    line_end: int = 0


MockStrategy = Literal["protocol", "subclass", "wrapper", "injection_needed", "none"]


class DependencyAssessment(BaseModel):
    type_name: str
    mockable: bool = False
    mock_strategy: MockStrategy = "none"
    reason: Optional[str] = None


class TestabilityResult(BaseModel):
    __test__ = False  # not a pytest test class despite the "Test" prefix

    class_name: str
    testable: bool
    testability_score: int = 0
    dependencies: list[DependencyAssessment] = Field(default_factory=list)
    blocking_issues: list[str] = Field(default_factory=list)
    refactoring_suggestions: list[str] = Field(default_factory=list)
    testable_methods: list[str] = Field(default_factory=list)
    untestable_methods: list[dict] = Field(default_factory=list)


class PrioritizedMethod(BaseModel):
    class_name: str
    method_name: str
    priority_score: int = 0
    reason: Optional[str] = None
    suggested_test_cases: list[str] = Field(default_factory=list)


class ContextBundle(BaseModel):
    target_file: Path
    target_class: str
    target_content: str
    related_contents: dict[str, str] = Field(default_factory=dict)
    analysis_summary: str = ""


class GeneratedTest(BaseModel):
    file_content: str
    target_class: str
    target_methods: list[str] = Field(default_factory=list)
    mock_code: Optional[str] = None
    output_path: Optional[Path] = None


ErrorCategory = Literal[
    "missing_import",
    "type_mismatch",
    "access_control",
    "mock_mismatch",
    "async_issue",
    "runtime_crash",
    "test_failure",
    "other",
]


class ParsedError(BaseModel):
    category: ErrorCategory = "other"
    file: Optional[str] = None
    line: Optional[int] = None
    column: Optional[int] = None
    message: str = ""
    symbol: Optional[str] = None
    test_name: Optional[str] = None
    severity: Optional[str] = None


class ValidationIteration(BaseModel):
    step: Literal["compile", "test"]
    success: bool
    errors: list[ParsedError] = Field(default_factory=list)
    raw_output: str = ""


class ValidationResult(BaseModel):
    compiled: bool = False
    passed: bool = False
    retry_count: int = 0
    iterations: list[ValidationIteration] = Field(default_factory=list)
    skipped: bool = False
    skipped_reason: Optional[str] = None


class PipelineReport(BaseModel):
    files_scanned: int = 0
    classes_analyzed: int = 0
    tests_generated: int = 0
    pass_rate: float = 0.0
    token_usage: dict = Field(default_factory=dict)
    estimated_cost: float = 0.0
