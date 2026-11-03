"""Unit tests for the pure parts of ``scripts.benchmark_models``.

Covers the recommendation ranking, JSON report loading with cost
recomputation, and markdown regeneration from the committed 2026-11-02
report. No database, no LLM calls.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from scripts.benchmark_models import (
    PRICING_USD_PER_M_TOKENS,
    STAGE_NAMES,
    ModelReport,
    StageStats,
    _pick_recommendations,
    load_json_report,
    write_markdown_report,
)

COMMITTED_JSON = Path("docs/benchmarks/2026-11-02-extract-models.json")


def _report(
    model: str,
    *,
    correct: int,
    grounded: int,
    latency_ms: int,
    input_tokens: int,
    output_tokens: int,
) -> ModelReport:
    """Build a one-stage-per-name report with ten scored calls per stage."""
    report = ModelReport(model=model)
    for stage in STAGE_NAMES:
        price = PRICING_USD_PER_M_TOKENS[model]
        report.by_stage[stage] = StageStats(
            stage=stage,
            n_calls=10,
            n_scored=10,
            n_correct=correct,
            n_evidence_grounded=grounded,
            n_evidence_with_quotes=10,
            total_latency_ms=latency_ms * 10,
            total_input_tokens=input_tokens,
            total_output_tokens=output_tokens,
            total_cost_usd=(
                input_tokens / 1e6 * price["input"]
                + output_tokens / 1e6 * price["output"]
            ),
        )
    return report


class TestPickRecommendations:
    def test_grounded_model_beats_faster_ungrounded_model(self) -> None:
        fast_ungrounded = _report(
            "gpt-5.4-mini", correct=10, grounded=0, latency_ms=1000,
            input_tokens=4000, output_tokens=800,
        )
        slow_grounded = _report(
            "gpt-6.1-terra", correct=10, grounded=10, latency_ms=2300,
            input_tokens=4000, output_tokens=800,
        )
        picks = _pick_recommendations(
            {"gpt-5.4-mini": fast_ungrounded, "gpt-6.1-terra": slow_grounded}
        )
        for stage in STAGE_NAMES:
            assert f"**{stage}** → `gpt-6.1-terra`" in picks

    def test_accuracy_still_outranks_grounding(self) -> None:
        accurate = _report(
            "gpt-5.4-mini", correct=10, grounded=0, latency_ms=1000,
            input_tokens=4000, output_tokens=800,
        )
        grounded_but_wrong = _report(
            "gpt-6.1-terra", correct=8, grounded=10, latency_ms=2300,
            input_tokens=4000, output_tokens=800,
        )
        picks = _pick_recommendations(
            {"gpt-5.4-mini": accurate, "gpt-6.1-terra": grounded_but_wrong}
        )
        assert "`gpt-6.1-terra`" not in picks
        assert picks.count("`gpt-5.4-mini`") == len(STAGE_NAMES)

    def test_tie_on_accuracy_and_grounding_goes_to_the_cheaper_model(self) -> None:
        terra = _report(
            "gpt-6.1-terra", correct=10, grounded=10, latency_ms=2300,
            input_tokens=4000, output_tokens=800,
        )
        sol = _report(
            "gpt-6.1-sol", correct=10, grounded=10, latency_ms=2200,
            input_tokens=4000, output_tokens=800,
        )
        picks = _pick_recommendations({"gpt-6.1-sol": sol, "gpt-6.1-terra": terra})
        assert "`gpt-6.1-sol`" not in picks
        assert picks.count("`gpt-6.1-terra`") == len(STAGE_NAMES)


class TestLoadJsonReport:
    def test_committed_report_loads_with_recomputed_costs(self) -> None:
        loaded = load_json_report(COMMITTED_JSON)
        assert loaded.n_cases == 10
        assert loaded.started_at.tzinfo is not None
        assert set(loaded.reports) >= {"gpt-5.4-mini", "gpt-6-luna", "gpt-6.1-terra", "gpt-6.1-sol"}
        terra = loaded.reports["gpt-6.1-terra"]
        price = PRICING_USD_PER_M_TOKENS["gpt-6.1-terra"]
        for stage in STAGE_NAMES:
            stats = terra.by_stage[stage]
            expected = (
                stats.total_input_tokens / 1e6 * price["input"]
                + stats.total_output_tokens / 1e6 * price["output"]
            )
            assert stats.total_cost_usd == expected
            assert stats.total_cost_usd > 0

    def test_loaded_cases_keep_their_scores(self) -> None:
        loaded = load_json_report(COMMITTED_JSON)
        mini = loaded.reports["gpt-5.4-mini"]
        assert len(mini.cases) == 10
        assert sum(1 for c in mini.cases if c.intent_correct) == mini.by_stage["intent"].n_correct


class TestMarkdownFromJson:
    def test_regenerated_markdown_prices_every_model_and_picks_by_grounding(
        self, tmp_path: Path
    ) -> None:
        loaded = load_json_report(COMMITTED_JSON)
        out = tmp_path / "report.md"
        write_markdown_report(
            loaded.reports, out, n_cases=loaded.n_cases, started_at=loaded.started_at
        )
        text = out.read_text()
        assert "$0.0000" not in text
        # The committed 2026-11-02 run: terra is the only model fully grounded
        # on intent and line items; on the customer stage gpt-6-luna ties it
        # at 100%/100% and is cheaper, so the mechanical pick is luna there.
        assert "- **intent** → `gpt-6.1-terra`" in text
        assert "- **customer** → `gpt-6-luna`" in text
        assert "- **line_items** → `gpt-6.1-terra`" in text
        assert "Ranked by accuracy, then evidence grounding" in text
        assert "keep one model across the three extraction stages" in text

    def test_markdown_header_uses_the_run_date(self, tmp_path: Path) -> None:
        started = datetime(2026, 11, 2, 21, 2, 45, tzinfo=timezone.utc)
        out = tmp_path / "report.md"
        write_markdown_report({}, out, n_cases=0, started_at=started)
        assert out.read_text().startswith("# Extract-stage model benchmark — 2026-11-02")
