from __future__ import annotations

import json
from pathlib import Path

from ..llm.client import ClaudeClient
from ..models import PrioritizedMethod, TestabilityResult


_TEMPLATE_PATH = Path(__file__).parent / "templates" / "prioritize.txt"


def _load_template() -> str:
    return _TEMPLATE_PATH.read_text()


def _serialize(testable_classes: list[TestabilityResult]) -> str:
    return json.dumps(
        [c.model_dump(mode="json") for c in testable_classes],
        default=str,
    )


def prioritize_methods(
    testable_classes: list[TestabilityResult],
    claude_client: ClaudeClient,
) -> list[PrioritizedMethod]:
    if not testable_classes:
        return []

    prompt = _load_template().replace("{classes_json}", _serialize(testable_classes))
    response = claude_client.call_json(
        [{"role": "user", "content": prompt}],
        call_type="analysis",
        max_tokens=8_000,
    )

    items = response if isinstance(response, list) else response.get("methods", [])
    ranked = [PrioritizedMethod.model_validate(item) for item in items]
    ranked.sort(key=lambda m: m.priority_score, reverse=True)
    return ranked


def apply_limit(ranked_methods: list[PrioritizedMethod], limit: int) -> list[PrioritizedMethod]:
    if limit <= 0:
        return []
    return ranked_methods[:limit]
