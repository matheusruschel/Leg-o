from __future__ import annotations

import re
from pathlib import Path

from ..llm.client import ClaudeClient
from ..models import ContextBundle, GeneratedTest


_GENERATE_TEMPLATE = Path(__file__).parent / "templates" / "generate.txt"
_FIX_TEMPLATE = Path(__file__).parent / "templates" / "fix.txt"

_FENCE_RE = re.compile(r"^\s*```(?:swift)?\s*\n|\n\s*```\s*$", re.IGNORECASE)


class InvalidTestOutput(ValueError):
    """Raised when generator output doesn't look like an XCTest file."""


def generate_tests(
    context: ContextBundle,
    claude_client: ClaudeClient,
    methods: list[str],
    module_name: str,
    max_tokens: int = 4096,
) -> GeneratedTest:
    template = _GENERATE_TEMPLATE.read_text()
    related_block = _format_related(context.related_contents)
    prompt = (
        template.replace("{target_content}", context.target_content)
        .replace("{related_contents}", related_block)
        .replace("{analysis_summary}", context.analysis_summary or "(none)")
        .replace("{class_name}", context.target_class)
        .replace("{method_list}", ", ".join(methods))
        .replace("{module_name}", module_name)
    )

    raw = claude_client.call(
        [{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        call_type="generation",
    )
    code = _strip_fences(raw)
    _validate_xctest(code)

    return GeneratedTest(
        file_content=code,
        target_class=context.target_class,
        target_methods=methods,
    )


def fix_tests(
    failing_test: GeneratedTest,
    error_output: str,
    source_content: str,
    claude_client: ClaudeClient,
    max_tokens: int = 4096,
) -> GeneratedTest:
    template = _FIX_TEMPLATE.read_text()
    prompt = (
        template.replace("{test_content}", failing_test.file_content)
        .replace("{error_output}", error_output)
        .replace("{source_content}", source_content)
    )
    raw = claude_client.call(
        [{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        call_type="fix",
    )
    code = _strip_fences(raw)
    _validate_xctest(code)
    return GeneratedTest(
        file_content=code,
        target_class=failing_test.target_class,
        target_methods=failing_test.target_methods,
        output_path=failing_test.output_path,
    )


def write_test_file(generated_test: GeneratedTest, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{generated_test.target_class}Tests.swift"
    content = generated_test.file_content
    if not content.endswith("\n"):
        content += "\n"
    path.write_text(content)
    generated_test.output_path = path
    return path


def _format_related(related: dict[str, str]) -> str:
    if not related:
        return "(none)"
    chunks = []
    for path, content in related.items():
        chunks.append(f"--- {path} ---\n{content}")
    return "\n\n".join(chunks)


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if "```" not in stripped:
        return stripped
    parts = stripped.split("```")
    middles = parts[1::2]
    if not middles:
        return stripped
    candidate = max(middles, key=len)
    candidate = re.sub(r"^swift\s*\n", "", candidate, count=1, flags=re.IGNORECASE)
    return candidate.strip()


def _validate_xctest(code: str) -> None:
    if "import XCTest" not in code:
        raise InvalidTestOutput("missing `import XCTest`")
    if not re.search(r"class\s+\w+\s*:\s*XCTestCase", code) and not re.search(
        r"class\s+\w*Tests\b", code
    ):
        raise InvalidTestOutput("no XCTestCase subclass found")
    if not re.search(r"func\s+test\w*", code):
        raise InvalidTestOutput("no test methods found")
