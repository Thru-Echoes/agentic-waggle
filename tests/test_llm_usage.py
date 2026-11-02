"""Usage accounting: raw-client records, pricing, scoping. No network."""
from __future__ import annotations

from decimal import Decimal

from providers import llm


def test_records_outside_a_scope_are_dropped() -> None:
    llm.record_usage("gpt-6.1-terra", 10, 5)  # no scope active: must not raise or leak
    with llm.usage_scope("t") as u:
        pass
    assert u.summary.input_tokens == 0 and u.summary.by_model == {}


def test_scope_sums_raw_records_and_prices_them() -> None:
    with llm.usage_scope("copilot_turn") as u:
        llm.record_usage("gpt-6.1-terra", 1_000_000, 0)
        llm.record_usage("gpt-6.1-terra", 0, 200_000)
        llm.record_usage("gpt-5.4-mini", 2_000_000, 0)
    s = u.summary
    assert s.scope == "copilot_turn"
    assert s.input_tokens == 3_000_000 and s.output_tokens == 200_000
    assert s.by_model["gpt-6.1-terra"].calls == 2
    # terra: $1/M in + $5/M out; 5.4-mini: $0.15/M in
    assert s.cost_usd == Decimal("1.000000") + Decimal("1.000000") + Decimal("0.300000")
    assert s.priced is True


def test_unknown_model_is_reported_unpriced_not_guessed() -> None:
    with llm.usage_scope("x") as u:
        llm.record_usage("some-future-model", 1_000_000, 1_000_000)
        llm.record_usage("gpt-6-luna", 1_000_000, 0)
    assert u.summary.priced is False
    assert u.summary.cost_usd == Decimal("0.100000")  # only the priced model counts


def test_dated_model_ids_match_their_base_price() -> None:
    assert llm._price_for("gpt-5.4-mini-2026-03-17") == llm.MODEL_PRICES_USD_PER_M["gpt-5.4-mini"]
    assert llm._price_for("gpt-6.1-terra") == (1.0, 5.0)
    assert llm._price_for("nope") is None


def test_scopes_do_not_leak_into_each_other() -> None:
    with llm.usage_scope("a") as a:
        llm.record_usage("gpt-6-luna", 10, 1)
    with llm.usage_scope("b") as b:
        pass
    assert a.summary.input_tokens == 10 and b.summary.input_tokens == 0
