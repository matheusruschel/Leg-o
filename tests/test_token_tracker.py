from lego.llm.token_tracker import TokenTracker


def test_records_and_totals():
    t = TokenTracker(model="default")
    t.record("analysis", 100, 50)
    t.record("analysis", 200, 80)
    t.record("generation", 1000, 400)

    assert t.total_input_tokens == 1300
    assert t.total_output_tokens == 530
    assert t.total_calls == 3


def test_report_breakdown_and_cost():
    t = TokenTracker(model="default", pricing={"default": (3.0, 15.0)})
    t.record("analysis", 1_000_000, 0)
    t.record("generation", 0, 1_000_000)

    rep = t.report()
    assert rep["by_type"]["analysis"]["calls"] == 1
    assert rep["by_type"]["generation"]["output_tokens"] == 1_000_000
    # 1M in @ $3 + 1M out @ $15 = $18
    assert rep["estimated_cost_usd"] == 18.0


def test_unknown_model_falls_back_to_default_pricing():
    t = TokenTracker(model="claude-mystery", pricing={"default": (1.0, 2.0)})
    t.record("analysis", 1_000_000, 1_000_000)
    assert t.estimated_cost() == 3.0
