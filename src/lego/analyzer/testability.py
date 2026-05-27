from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable

from ..llm.client import ClaudeClient
from ..models import ClassMetadata, TestabilityResult

log = logging.getLogger(__name__)


_TEMPLATE_PATH = Path(__file__).parent / "templates" / "testability.txt"
_BATCH_SIZE = 50
# Each assessment is ~400 output tokens (multiple lists per class). At batch_size=50
# we need ~20K headroom. Sonnet's 8K default would truncate; size up generously.
_ANALYZE_MAX_TOKENS = 16_000


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

    batches = list(_batches(class_metadata_list, batch_size))
    for i, batch in enumerate(batches, start=1):
        log.info("analyze batch %d/%d (%d classes) — calling Claude ...",
                 i, len(batches), len(batch))
        prompt = template.replace("{classes_json}", _serialize(batch))
        response = claude_client.call_json(
            [{"role": "user", "content": prompt}],
            call_type="analysis",
            max_tokens=_ANALYZE_MAX_TOKENS,
        )
        assessments = response.get("assessments", []) if isinstance(response, dict) else []
        for item in assessments:
            results.append(TestabilityResult.model_validate(item))

    return results


def filter_testable(results: list[TestabilityResult]) -> list[TestabilityResult]:
    return [r for r in results if r.testable or r.testable_methods]
