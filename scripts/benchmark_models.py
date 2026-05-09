"""Benchmark candidate LLM models across the order-extraction stages.

Probes each candidate against the live OpenAI API (gracefully skipping
models that 404), runs each available model against a curated ground-truth
set of fixture emails, and reports per-stage latency / cost / accuracy.

The output drives per-stage model assignment in ``app/config.Settings``.

Stages benchmarked:
    1. classify_intent       — IntentClassification
    2. extract_customer      — CustomerReference
    3. extract_line_items    — list[RequestedLineItem] (via _LineItemsEnvelope)

Ground truth lives in ``tests/ground_truth.json``.

Usage:
    # Run all candidates against the full ground-truth set:
    python -m scripts.benchmark_models

    # Restrict to specific stages or models:
    python -m scripts.benchmark_models --stages intent
    python -m scripts.benchmark_models --models gpt-4o-mini,gpt-5.4-mini

Requires:
    - OPENAI_API_KEY in env
    - gmail-fixtures available at /gmail-fixtures (in container)
      or ./gmail-fixtures (when run locally)

Outputs:
    - docs/benchmarks/2026-05-08-extract-models.md
    - docs/benchmarks/2026-05-08-extract-models.json
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openai import APIError, NotFoundError, OpenAI
from pydantic import BaseModel, Field, ValidationError

from agents.schemas import (
    CustomerReference,
    Grounded,
    IntentClassification,
    RequestedLineItem,
)

log = logging.getLogger("benchmark_models")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


CANDIDATE_MODELS = [
    "gpt-4o-mini",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    "gpt-5.5",
    "gpt-5.5-mini",
    "gpt-5.5-nano",
]

# Approximate $/M tokens — best-effort placeholders. The benchmark records
# raw token counts so cost can be re-derived from authoritative pricing.
PRICING_USD_PER_M_TOKENS: dict[str, dict[str, float]] = {
    "gpt-4o-mini":  {"input": 0.150, "output": 0.600},
    "gpt-5.4-mini": {"input": 0.150, "output": 0.600},
    "gpt-5.4-nano": {"input": 0.075, "output": 0.300},
    "gpt-5.5":      {"input": 2.500, "output": 10.000},
    "gpt-5.5-mini": {"input": 0.250, "output": 1.000},
    "gpt-5.5-nano": {"input": 0.100, "output": 0.400},
}

STAGE_NAMES = ("intent", "customer", "line_items")


def _pricing_for(model: str) -> dict[str, float]:
    return PRICING_USD_PER_M_TOKENS.get(model, {"input": 0.0, "output": 0.0})


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------


FIXTURE_ROOTS = (Path("/gmail-fixtures"), Path("./gmail-fixtures"))


def _fixture_root() -> Path:
    for p in FIXTURE_ROOTS:
        if p.exists():
            return p
    raise FileNotFoundError(
        f"No gmail-fixtures directory found. Tried: {[str(p) for p in FIXTURE_ROOTS]}"
    )


def load_email(account_id: str, message_id: str) -> dict[str, Any]:
    """Read one fixture email by (account_id, message_id). Returns raw dict."""
    root = _fixture_root()
    fpath = root / f"{account_id}.inbox.json"
    if not fpath.exists():
        raise FileNotFoundError(f"Fixture file not found: {fpath}")
    rows = json.loads(fpath.read_text())
    for r in rows:
        if r["id"] == message_id:
            return r
    raise LookupError(f"message_id {message_id} not found in {fpath}")


# ---------------------------------------------------------------------------
# Ground truth
# ---------------------------------------------------------------------------


GROUND_TRUTH_PATH = Path("tests/ground_truth.json")


@dataclass(frozen=True)
class GroundTruthCase:
    account_id: str
    message_id: str
    expected_intent: str
    expected_customer_substring: str | None
    expected_line_item_count_min: int


def load_ground_truth() -> list[GroundTruthCase]:
    """Read curated ground-truth cases. Raises FileNotFoundError loudly."""
    if not GROUND_TRUTH_PATH.exists():
        raise FileNotFoundError(f"Ground truth not found: {GROUND_TRUTH_PATH}")
    payload = json.loads(GROUND_TRUTH_PATH.read_text())
    return [GroundTruthCase(**c) for c in payload["cases"]]


# ---------------------------------------------------------------------------
# Model probing
# ---------------------------------------------------------------------------


def probe_available_models(client: OpenAI, candidates: list[str]) -> list[str]:
    """Return the subset of candidates the API will accept.

    A model is "available" if a small completion request succeeds. Models
    that 404 are skipped. Models that BadRequest only because the probe
    was too small (e.g. gpt-5.x needs > 1 completion token) are still
    treated as available — the actual stage runs will exercise them.
    """
    available: list[str] = []
    for name in candidates:
        try:
            client.chat.completions.create(
                model=name,
                messages=[{"role": "user", "content": "ping"}],
                max_completion_tokens=50,
            )
            available.append(name)
            log.info("model available: %s", name)
        except NotFoundError:
            log.info("model not available (404): %s", name)
        except APIError as exc:
            msg = str(exc).lower()
            # Token-budget rejections are still "available" — the model
            # exists and accepted the request, it just needs more room.
            if "max_completion_tokens" in msg or "could not finish" in msg:
                available.append(name)
                log.info("model available (probe too small): %s", name)
            else:
                log.warning("model probe error for %s: %s", name, exc)
    return available


# ---------------------------------------------------------------------------
# Prompts (kept in sync with agents/order_extractor.py)
# ---------------------------------------------------------------------------


def _intent_prompt(email: dict[str, Any]) -> str:
    return (
        "Classify this email's intent.\n\n"
        f"Subject: {email['subject']}\n"
        f"From: {email['from_address']}\n"
        f"Body:\n{email['body_text']}\n\n"
        "Fields to fill:\n"
        "- intent: one of order_request | inquiry | complaint | other\n"
        "- reasoning: one short sentence\n"
        "- evidence: 1-2 verbatim quotes from the body that justify your choice\n"
        "- confidence: 0.0-1.0 self-rated"
    )


def _customer_prompt(email: dict[str, Any]) -> str:
    return (
        "Find the customer or company this email is about.\n\n"
        f"Subject: {email['subject']}\n"
        f"From: {email['from_address']}\n"
        f"Body:\n{email['body_text']}\n\n"
        "Fields to fill:\n"
        "- mentioned_name: verbatim — do NOT correct spelling or expand abbreviations\n"
        "- requester_is_customer: true unless the sender is a broker or third party\n"
        "- evidence: 1-2 verbatim quotes from the body containing the company name\n"
        "- confidence: 0.0-1.0 self-rated"
    )


def _line_items_prompt(email: dict[str, Any]) -> str:
    return (
        "Extract every line item the email requests for purchase.\n\n"
        f"Body:\n{email['body_text']}\n\n"
        "For each item, fill these fields:\n"
        "- description: the noun phrase ONLY. DO NOT include the quantity.\n"
        "    correct:   {description: \"hives\",         quantity: 40}\n"
        "    incorrect: {description: \"40 hives\",      quantity: 40}\n"
        "    incorrect: {description: \"a dozen queens\", quantity: 12}\n"
        "- quantity: integer >= 1; infer 'a few' = 3, 'dozen' = 12, etc.\n"
        "- mentioned_sku: SKU code ONLY if literally cited in the email\n"
        "  (e.g. POL_ALMOND_PREMIUM). Otherwise null.\n"
        "- mentioned_unit_price_usd: only if the email states a price for\n"
        "  this specific item. Otherwise null.\n"
        "- evidence: 1-2 verbatim quotes from the body justifying THIS item.\n"
        "- confidence: 0.0-1.0 self-rated.\n\n"
        "Return an empty items list if no specific items are requested."
    )


class _LineItemsEnvelope(Grounded):
    """Wrapper schema so structured output returns a list cleanly."""

    items: list[RequestedLineItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Stage runners
# ---------------------------------------------------------------------------


@dataclass
class CallMetrics:
    model: str
    stage: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    error: str | None = None


def _compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    p = _pricing_for(model)
    return (
        input_tokens / 1_000_000 * p["input"]
        + output_tokens / 1_000_000 * p["output"]
    )


def _run_stage(
    client: OpenAI,
    model: str,
    stage: str,
    prompt: str,
    schema: type[BaseModel],
) -> tuple[BaseModel | None, CallMetrics]:
    """Run one stage on one model. Returns (parsed_output, metrics).

    On API error (model rejects the request, structured-output fails, etc.),
    returns (None, metrics_with_error).
    """
    t0 = time.perf_counter()
    try:
        completion = client.beta.chat.completions.parse(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format=schema,
        )
    except (APIError, NotFoundError) as exc:
        latency = int((time.perf_counter() - t0) * 1000)
        return None, CallMetrics(
            model=model, stage=stage, latency_ms=latency,
            input_tokens=0, output_tokens=0, cost_usd=0.0,
            error=f"api: {str(exc)[:160]}",
        )
    except ValidationError as exc:
        # Model returned content that didn't satisfy our Pydantic schema —
        # data point about the model's ability to follow strict structured
        # output. Record as error and continue.
        latency = int((time.perf_counter() - t0) * 1000)
        return None, CallMetrics(
            model=model, stage=stage, latency_ms=latency,
            input_tokens=0, output_tokens=0, cost_usd=0.0,
            error=f"validation: {str(exc)[:160]}",
        )

    latency = int((time.perf_counter() - t0) * 1000)
    msg = completion.choices[0].message
    parsed = msg.parsed
    usage = completion.usage
    in_t = usage.prompt_tokens if usage else 0
    out_t = usage.completion_tokens if usage else 0
    return parsed, CallMetrics(
        model=model, stage=stage, latency_ms=latency,
        input_tokens=in_t, output_tokens=out_t,
        cost_usd=_compute_cost(model, in_t, out_t),
    )


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score_intent(predicted: IntentClassification | None, expected: str) -> bool:
    if predicted is None:
        return False
    return predicted.intent == expected


def score_customer(
    predicted: CustomerReference | None,
    expected_substring: str | None,
) -> bool | None:
    """True if predicted name contains the expected substring (case-insensitive).
    None if no expected substring was specified — case is unscored."""
    if expected_substring is None:
        return None
    if predicted is None:
        return False
    return expected_substring.lower() in predicted.mentioned_name.lower()


def score_line_items(
    predicted: _LineItemsEnvelope | None,
    expected_min: int,
) -> bool:
    if predicted is None:
        return expected_min == 0
    return len(predicted.items) >= expected_min


def evidence_grounded(
    predicted: Grounded | None,
    source_text: str,
) -> bool | None:
    """Verify that each evidence quote appears literally in source_text.

    Returns True if all quotes appear (case-sensitive substring), False if
    any quote is absent (a hallucination), None if no quotes were given.
    """
    if predicted is None or not predicted.evidence:
        return None
    return all(q in source_text for q in predicted.evidence)


# ---------------------------------------------------------------------------
# Top-level run
# ---------------------------------------------------------------------------


@dataclass
class CaseResult:
    account_id: str
    message_id: str
    expected_intent: str
    intent_correct: bool | None = None
    customer_correct: bool | None = None
    line_items_correct: bool | None = None
    intent_evidence_grounded: bool | None = None
    customer_evidence_grounded: bool | None = None
    line_items_evidence_grounded: bool | None = None


@dataclass
class StageStats:
    stage: str
    n_calls: int = 0       # how many times the stage was run (for latency / cost)
    n_scored: int = 0      # how many of those had a scoreable ground-truth field
    n_correct: int = 0     # of n_scored, how many were judged correct
    n_evidence_grounded: int = 0
    n_evidence_with_quotes: int = 0
    total_latency_ms: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)

    def accuracy(self) -> float:
        # Accuracy denominator is n_scored, not n_calls. Cases that don't
        # have a ground-truth assertion for this stage (e.g.
        # expected_customer_substring=null) are excluded.
        return self.n_correct / max(1, self.n_scored)

    def evidence_groundedness(self) -> float:
        return self.n_evidence_grounded / max(1, self.n_evidence_with_quotes)

    def avg_latency_ms(self) -> int:
        return self.total_latency_ms // max(1, self.n_calls)


@dataclass
class ModelReport:
    model: str
    by_stage: dict[str, StageStats] = field(default_factory=dict)
    cases: list[CaseResult] = field(default_factory=list)


def run_benchmark(
    client: OpenAI,
    models: list[str],
    cases: list[GroundTruthCase],
    stages: tuple[str, ...] = STAGE_NAMES,
) -> dict[str, ModelReport]:
    """Run the benchmark. Returns one ModelReport per model.

    Side effects: many LLM calls.
    """
    out: dict[str, ModelReport] = {}
    for model in models:
        report = ModelReport(model=model)
        for s in stages:
            report.by_stage[s] = StageStats(stage=s)

        for c in cases:
            email = load_email(c.account_id, c.message_id)
            case_result = CaseResult(
                account_id=c.account_id,
                message_id=c.message_id,
                expected_intent=c.expected_intent,
            )

            if "intent" in stages:
                pred, m = _run_stage(
                    client, model, "intent",
                    _intent_prompt(email), IntentClassification,
                )
                stats = report.by_stage["intent"]
                stats.n_calls += 1
                stats.total_latency_ms += m.latency_ms
                stats.total_input_tokens += m.input_tokens
                stats.total_output_tokens += m.output_tokens
                stats.total_cost_usd += m.cost_usd
                if m.error:
                    stats.errors.append(m.error)
                ok = score_intent(pred, c.expected_intent)  # type: ignore[arg-type]
                case_result.intent_correct = ok
                stats.n_scored += 1
                if ok:
                    stats.n_correct += 1
                grounded = evidence_grounded(pred, email["body_text"])  # type: ignore[arg-type]
                case_result.intent_evidence_grounded = grounded
                if grounded is not None:
                    stats.n_evidence_with_quotes += 1
                    if grounded:
                        stats.n_evidence_grounded += 1

            if "customer" in stages:
                pred, m = _run_stage(
                    client, model, "customer",
                    _customer_prompt(email), CustomerReference,
                )
                stats = report.by_stage["customer"]
                stats.n_calls += 1
                stats.total_latency_ms += m.latency_ms
                stats.total_input_tokens += m.input_tokens
                stats.total_output_tokens += m.output_tokens
                stats.total_cost_usd += m.cost_usd
                if m.error:
                    stats.errors.append(m.error)
                cust_ok = score_customer(pred, c.expected_customer_substring)  # type: ignore[arg-type]
                case_result.customer_correct = cust_ok
                if cust_ok is not None:
                    stats.n_scored += 1
                    if cust_ok is True:
                        stats.n_correct += 1
                grounded = evidence_grounded(pred, email["body_text"])  # type: ignore[arg-type]
                case_result.customer_evidence_grounded = grounded
                if grounded is not None:
                    stats.n_evidence_with_quotes += 1
                    if grounded:
                        stats.n_evidence_grounded += 1

            if "line_items" in stages:
                pred, m = _run_stage(
                    client, model, "line_items",
                    _line_items_prompt(email), _LineItemsEnvelope,
                )
                stats = report.by_stage["line_items"]
                stats.n_calls += 1
                stats.total_latency_ms += m.latency_ms
                stats.total_input_tokens += m.input_tokens
                stats.total_output_tokens += m.output_tokens
                stats.total_cost_usd += m.cost_usd
                if m.error:
                    stats.errors.append(m.error)
                items_ok = score_line_items(pred, c.expected_line_item_count_min)  # type: ignore[arg-type]
                case_result.line_items_correct = items_ok
                stats.n_scored += 1
                if items_ok:
                    stats.n_correct += 1
                grounded = evidence_grounded(pred, email["body_text"])  # type: ignore[arg-type]
                case_result.line_items_evidence_grounded = grounded
                if grounded is not None:
                    stats.n_evidence_with_quotes += 1
                    if grounded:
                        stats.n_evidence_grounded += 1

            report.cases.append(case_result)

        out[model] = report
        log.info(
            "model=%s done. intent_acc=%.2f cust_acc=%.2f items_acc=%.2f cost=$%.4f",
            model,
            report.by_stage.get("intent", StageStats("intent")).accuracy(),
            report.by_stage.get("customer", StageStats("customer")).accuracy(),
            report.by_stage.get("line_items", StageStats("line_items")).accuracy(),
            sum(s.total_cost_usd for s in report.by_stage.values()),
        )

    return out


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _markdown_table(rows: list[list[str]], headers: list[str]) -> str:
    out = ["| " + " | ".join(headers) + " |"]
    out.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for r in rows:
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)


def write_markdown_report(
    reports: dict[str, ModelReport],
    out_path: Path,
    *,
    n_cases: int,
    started_at: datetime,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append(f"# Extract-stage model benchmark — {started_at.date().isoformat()}\n")
    lines.append(
        f"Ran {len(reports)} candidate models against {n_cases} ground-truth "
        f"cases across {len(STAGE_NAMES)} stages."
    )
    lines.append(f"Started at {started_at.isoformat()}.\n")

    # Per-stage comparison table
    for stage in STAGE_NAMES:
        lines.append(f"\n## Stage: {stage}\n")
        rows: list[list[str]] = []
        for model, report in reports.items():
            stats = report.by_stage.get(stage, StageStats(stage))
            rows.append([
                f"`{model}`",
                f"{stats.accuracy():.0%}" if stats.n_calls else "—",
                f"{stats.evidence_groundedness():.0%}" if stats.n_evidence_with_quotes else "—",
                f"{stats.avg_latency_ms()}",
                f"${stats.total_cost_usd:.4f}",
                f"{stats.total_input_tokens + stats.total_output_tokens}",
            ])
        lines.append(_markdown_table(
            rows,
            ["model", "accuracy", "evidence-grounded", "avg latency (ms)",
             "total cost", "total tokens"],
        ))

    # Per-model totals
    lines.append("\n## Per-model totals\n")
    rows = []
    for model, report in reports.items():
        total_correct = sum(s.n_correct for s in report.by_stage.values())
        total_scored = sum(s.n_scored for s in report.by_stage.values())
        total_calls = sum(s.n_calls for s in report.by_stage.values())
        total_cost = sum(s.total_cost_usd for s in report.by_stage.values())
        total_lat = sum(s.total_latency_ms for s in report.by_stage.values())
        avg_lat = total_lat // max(1, total_calls)
        rows.append([
            f"`{model}`",
            f"{total_correct}/{total_scored}",
            f"{total_correct/max(1,total_scored):.0%}",
            f"{avg_lat}",
            f"${total_cost:.4f}",
        ])
    lines.append(_markdown_table(
        rows,
        ["model", "correct/scored", "overall accuracy", "avg latency (ms)", "total cost"],
    ))

    # Picks
    lines.append("\n## Recommended per-stage assignment\n")
    lines.append(_pick_recommendations(reports))

    out_path.write_text("\n".join(lines) + "\n")
    log.info("wrote markdown report → %s", out_path)


def _pick_recommendations(reports: dict[str, ModelReport]) -> str:
    """Pick the best model per stage by (accuracy, then latency, then cost)."""
    lines = []
    for stage in STAGE_NAMES:
        ranked = sorted(
            reports.items(),
            key=lambda kv: (
                -kv[1].by_stage.get(stage, StageStats(stage)).accuracy(),
                kv[1].by_stage.get(stage, StageStats(stage)).avg_latency_ms(),
                kv[1].by_stage.get(stage, StageStats(stage)).total_cost_usd,
            ),
        )
        if not ranked:
            continue
        winner_name, winner = ranked[0]
        s = winner.by_stage.get(stage, StageStats(stage))
        lines.append(
            f"- **{stage}** → `{winner_name}` "
            f"(accuracy {s.accuracy():.0%}, "
            f"avg {s.avg_latency_ms()}ms, "
            f"${s.total_cost_usd:.4f} for {s.n_calls} calls)"
        )
    return "\n".join(lines)


def write_json_report(
    reports: dict[str, ModelReport],
    out_path: Path,
    started_at: datetime,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "started_at": started_at.isoformat(),
        "models": {
            name: {
                "by_stage": {
                    s: asdict(stats) for s, stats in r.by_stage.items()
                },
                "cases": [asdict(c) for c in r.cases],
            }
            for name, r in reports.items()
        },
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str))
    log.info("wrote json report → %s", out_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", default=",".join(CANDIDATE_MODELS))
    parser.add_argument("--stages", default=",".join(STAGE_NAMES))
    parser.add_argument(
        "--out-dir", default="docs/benchmarks",
        help="Where to write the markdown + JSON outputs.",
    )
    args = parser.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY must be set")

    client = OpenAI()
    requested = [m.strip() for m in args.models.split(",") if m.strip()]
    requested_stages = tuple(s.strip() for s in args.stages.split(",") if s.strip())

    available = probe_available_models(client, requested)
    if not available:
        raise SystemExit(f"None of the requested models are available: {requested}")

    cases = load_ground_truth()
    log.info(
        "running benchmark: models=%s stages=%s n_cases=%s",
        available, requested_stages, len(cases),
    )

    started = datetime.now(timezone.utc)
    reports = run_benchmark(client, available, cases, stages=requested_stages)

    out_dir = Path(args.out_dir)
    date = started.date().isoformat()
    write_markdown_report(reports, out_dir / f"{date}-extract-models.md",
                          n_cases=len(cases), started_at=started)
    write_json_report(reports, out_dir / f"{date}-extract-models.json", started_at=started)


if __name__ == "__main__":
    main()
