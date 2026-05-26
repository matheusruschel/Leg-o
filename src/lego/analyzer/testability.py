from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from ..llm.client import ClaudeClient
from ..models import ClassMetadata, TestabilityResult


_TEMPLATE_PATH = Path(__file__).parent / "templates" / "testability.txt"
_BATCH_SIZE = 50


def _load_template() -> str:
    return _TEMPLATE_PATH.read_text()


def _batches(items: list[ClassMetadata], size: int) -> Iterable[list[ClassMetadata]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _serialize(classes: list[ClassMetadata]) -> str:
    return json.dumps([c.model_dump(mode="json") for c in classes], default=str)


def assess_testability(
    class_metadata_list: list[ClassMetadata],
    claude_client: ClaudeClient,
    batch_size: int = _BATCH_SIZE,
) -> list[TestabilityResult]:
    template = _load_template()
    results: list[TestabilityResult] = []

    for batch in _batches(class_metadata_list, batch_size):
        prompt = template.replace("{classes_json}", _serialize(batch))
        response = claude_client.call_json(
            [{"role": "user", "content": prompt}],
            call_type="analysis",
        )
        assessments = response.get("assessments", []) if isinstance(response, dict) else []
        for item in assessments:
            results.append(TestabilityResult.model_validate(item))

    return results


def filter_testable(results: list[TestabilityResult]) -> list[TestabilityResult]:
    return [r for r in results if r.testable or r.testable_methods]
