from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lego.analyzer import (
    apply_limit,
    assess_testability,
    filter_testable,
    prioritize_methods,
)
from lego.models import ClassMetadata, PrioritizedMethod, TestabilityResult


FIXTURES = Path(__file__).parent / "fixtures" / "expected_ast"


def _load_fixture(name: str) -> list[ClassMetadata]:
    raw = json.loads((FIXTURES / name).read_text())
    return [ClassMetadata.model_validate(c) for c in raw]


def _assessment(class_name: str, **overrides):
    base = {
        "class_name": class_name,
        "testable": True,
        "testability_score": 80,
        "dependencies": [],
        "blocking_issues": [],
        "refactoring_suggestions": [],
        "testable_methods": ["doThing"],
        "untestable_methods": [],
    }
    base.update(overrides)
    return base


def test_assess_testability_parses_response_into_models():
    classes = _load_fixture("network_service.json")
    client = MagicMock()
    client.call_json.return_value = {
        "assessments": [
            _assessment("NetworkServiceProtocol", testable=False, testable_methods=[]),
            _assessment("NetworkService"),
        ]
    }

    results = assess_testability(classes, client)

    assert [r.class_name for r in results] == ["NetworkServiceProtocol", "NetworkService"]
    assert results[1].testable is True
    client.call_json.assert_called_once()
    # Prompt should contain the serialized class JSON
    sent_prompt = client.call_json.call_args.args[0][0]["content"]
    assert "NetworkService" in sent_prompt


def test_assess_testability_batches_when_over_limit():
    classes = _load_fixture("network_service.json") * 30  # 60 entries
    client = MagicMock()
    client.call_json.return_value = {"assessments": []}

    assess_testability(classes, client, batch_size=25)

    assert client.call_json.call_count == 3  # 25 + 25 + 10


def test_filter_testable_keeps_classes_with_at_least_some_testable_methods():
    results = [
        TestabilityResult(class_name="A", testable=True),
        TestabilityResult(class_name="B", testable=False, testable_methods=["foo"]),
        TestabilityResult(class_name="C", testable=False),
    ]
    kept = filter_testable(results)
    assert [r.class_name for r in kept] == ["A", "B"]


def test_prioritize_methods_returns_sorted_models():
    testable = [TestabilityResult(class_name="A", testable=True, testable_methods=["a", "b"])]
    client = MagicMock()
    client.call_json.return_value = [
        {"class_name": "A", "method_name": "a", "priority_score": 40, "reason": "ok"},
        {"class_name": "A", "method_name": "b", "priority_score": 90, "reason": "complex"},
    ]

    ranked = prioritize_methods(testable, client)

    assert [m.method_name for m in ranked] == ["b", "a"]
    assert ranked[0].priority_score == 90


def test_prioritize_methods_empty_input_skips_api_call():
    client = MagicMock()
    assert prioritize_methods([], client) == []
    client.call_json.assert_not_called()


def test_prioritize_methods_accepts_dict_wrapper():
    testable = [TestabilityResult(class_name="A", testable=True)]
    client = MagicMock()
    client.call_json.return_value = {
        "methods": [{"class_name": "A", "method_name": "a", "priority_score": 10}]
    }
    ranked = prioritize_methods(testable, client)
    assert ranked[0].method_name == "a"


def test_apply_limit():
    methods = [
        PrioritizedMethod(class_name="A", method_name=f"m{i}", priority_score=100 - i)
        for i in range(10)
    ]
    assert len(apply_limit(methods, 3)) == 3
    assert apply_limit(methods, 0) == []
    assert len(apply_limit(methods, 999)) == 10
