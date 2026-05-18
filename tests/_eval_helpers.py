"""Eval harness helpers for the semantic-matcher corpus.

Provides:
    load_corpus()              -- load + validate all cases from YAML
    stratified_sample()        -- per-category 20% sample (EVAL_MODE=sample)
    evaluate_case_customer()   -- run one customer case through the live matcher
    evaluate_case_sku()        -- run one SKU case through the live matcher
    write_report()             -- write a Markdown eval report to .reports/
    corpus_sha()               -- SHA-256 of the corpus YAML (for versioning)
    resolve_git_sha()          -- current HEAD git SHA

Public exports:
    CUSTOMER_THRESHOLDS, SKU_THRESHOLDS -- per-category precision floors
    CORPUS_PATH, REPORTS_DIR
    EvalCase, EvalResult, CategoryResult

Side effects:
    write_report writes to .reports/<name>.md (creates dir if absent).
    evaluate_case_* call the live matcher (DB reads + OpenAI API calls).
"""
from __future__ import annotations

import hashlib
import os
import random
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

CORPUS_PATH = Path("tests/fixtures/matcher_corpus.yaml")
REPORTS_DIR = Path(".reports")

EVAL_MODE = os.environ.get("MATCHER_EVAL_MODE", "sample")
SAMPLE_FRACTION = 0.20
SAMPLE_SEED = 42

# ---------------------------------------------------------------------------
# Per-category precision thresholds
# ---------------------------------------------------------------------------

CUSTOMER_THRESHOLDS: dict[str, float] = {
    "canonical": 1.00,
    "abbreviation": 0.98,
    "possessive": 0.98,
    "alias": 0.98,
    "apostrophe_variant": 0.98,
    "site_reference": 0.95,
    "site_shortform": 0.95,
    "typo_resolvable": 0.90,
    "long_form_sentence": 0.95,
    "pronoun_resolution": 0.85,
    "cross_customer_collision": 0.90,
    "ambiguous_reference": 0.95,
    "negative_case": 1.00,
    "generic_words": 1.00,
    "compound_reference": 0.85,
    "casual_colloquial": 0.95,
    "partial_match_noise": 0.95,
}

SKU_THRESHOLDS: dict[str, float] = {
    "sku_canonical_code": 1.00,
    "sku_product_name": 0.98,
    "sku_qty_plus_product": 0.98,
    "sku_service": 0.95,
    "sku_colloquial": 0.90,
    "sku_ambiguous": 0.95,
    "sku_negative": 1.00,
}

_SKU_CATEGORIES: frozenset[str] = frozenset(SKU_THRESHOLDS.keys())
_CUSTOMER_CATEGORIES: frozenset[str] = frozenset(CUSTOMER_THRESHOLDS.keys())


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class EvalCase:
    """One corpus case loaded from YAML.

    No side effects — pure data container.
    """

    input: str
    category: str
    expected_customer_name: str | None = None
    expected_sku_kind: str | None = None
    expected_sku_code: str | None = None
    expected_alternatives_min: int | None = None
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def is_sku_case(self) -> bool:
        """True when this case belongs to a SKU category."""
        return self.category in _SKU_CATEGORIES


@dataclass
class EvalResult:
    """Outcome of running one EvalCase through the live matcher.

    No side effects — pure data container.
    """

    case: EvalCase
    passed: bool
    actual_customer_name: str | None = None
    actual_sku_kind: str | None = None
    actual_sku_code: str | None = None
    error: str | None = None


@dataclass
class CategoryResult:
    """Aggregated pass/fail counts for one category.

    No side effects — pure data container.
    """

    category: str
    total: int
    passed: int
    failures: list[EvalResult] = field(default_factory=list)

    @property
    def precision(self) -> float:
        """Fraction of cases that passed (0.0–1.0)."""
        return self.passed / self.total if self.total else 0.0


# ---------------------------------------------------------------------------
# Corpus loading
# ---------------------------------------------------------------------------


def load_corpus(path: Path = CORPUS_PATH) -> list[EvalCase]:
    """Load and parse all eval cases from the YAML corpus.

    Parameters:
        path: Path to matcher_corpus.yaml.

    Returns:
        List of EvalCase objects in corpus order.

    Side effects: reads the file from disk.

    Raises:
        FileNotFoundError: when the corpus file does not exist.
        ValueError: when a case has an unrecognised category.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Corpus file not found at {path}. "
            f"Did you forget to create tests/fixtures/matcher_corpus.yaml?"
        )

    raw = yaml.safe_load(path.read_text())
    cases_raw: list[dict[str, Any]] = raw.get("cases", [])

    known_categories = _CUSTOMER_CATEGORIES | _SKU_CATEGORIES
    cases: list[EvalCase] = []
    for i, item in enumerate(cases_raw):
        category = item.get("category", "")
        if category not in known_categories:
            raise ValueError(
                f"Corpus case {i} has unknown category {category!r}. "
                f"Known categories: {sorted(known_categories)}"
            )
        cases.append(
            EvalCase(
                input=item["input"],
                category=category,
                expected_customer_name=item.get("expected_customer_name"),
                expected_sku_kind=item.get("expected_sku_kind"),
                expected_sku_code=item.get("expected_sku_code"),
                expected_alternatives_min=item.get("expected_alternatives_min"),
                context=item.get("context") or {},
            )
        )
    return cases


# ---------------------------------------------------------------------------
# Stratified sampling
# ---------------------------------------------------------------------------


def stratified_sample(cases: list[EvalCase], fraction: float = SAMPLE_FRACTION) -> list[EvalCase]:
    """Return a per-category stratified sample of the corpus.

    For each category, samples `ceil(n * fraction)` cases with a fixed RNG
    seed so results are reproducible. Always returns at least 1 case per
    category to avoid division-by-zero in precision computation.

    Parameters:
        cases: Full list of EvalCase objects from load_corpus().
        fraction: Fraction to sample per category (default SAMPLE_FRACTION=0.20).

    Returns:
        Sampled list of EvalCase objects.

    Side effects: none (deterministic RNG under SAMPLE_SEED=42).
    """
    import math

    by_category: dict[str, list[EvalCase]] = {}
    for c in cases:
        by_category.setdefault(c.category, []).append(c)

    rng = random.Random(SAMPLE_SEED)
    sampled: list[EvalCase] = []
    for category, cat_cases in sorted(by_category.items()):
        k = max(1, math.ceil(len(cat_cases) * fraction))
        k = min(k, len(cat_cases))
        chosen = rng.sample(cat_cases, k)
        sampled.extend(chosen)

    return sampled


# ---------------------------------------------------------------------------
# Case evaluation
# ---------------------------------------------------------------------------


def _build_conversation_context(context_dict: dict[str, Any], db: Any) -> Any:
    """Construct a ConversationContext from a corpus case's context dict.

    Resolves active_account_name and last_3_customer_names to UUIDs via DB
    lookup. Missing names are silently skipped (a warning is sufficient;
    fail-loud only applies to mandatory FK references, not optional context).

    Parameters:
        context_dict: The 'context' field from an EvalCase.
        db: SQLAlchemy Session for name → id lookup.

    Returns:
        ConversationContext instance.

    Side effects: DB reads (SELECT from customers).
    """
    from sqlalchemy import text

    from agents.schemas import ConversationContext

    active_account_id: uuid.UUID | None = None
    last_3_customer_ids: list[uuid.UUID] = []

    active_name = context_dict.get("active_account_name")
    if active_name:
        row = db.execute(
            text("SELECT id FROM customers WHERE name = :n"),
            {"n": active_name},
        ).first()
        if row:
            active_account_id = uuid.UUID(str(row.id))

    for name in (context_dict.get("last_3_customer_names") or [])[:3]:
        row = db.execute(
            text("SELECT id FROM customers WHERE name = :n"),
            {"n": name},
        ).first()
        if row:
            last_3_customer_ids.append(uuid.UUID(str(row.id)))

    return ConversationContext(
        active_account_id=active_account_id,
        last_3_customer_ids=last_3_customer_ids,
    )


def _get_customer_name_by_id(customer_id: uuid.UUID, db: Any) -> str | None:
    """Look up a customer's canonical name by UUID.

    Parameters:
        customer_id: UUID to resolve.
        db: SQLAlchemy Session.

    Returns:
        Canonical name string, or None if not found.

    Side effects: DB read.
    """
    from sqlalchemy import text

    row = db.execute(
        text("SELECT name FROM customers WHERE id = :id"),
        {"id": customer_id},
    ).first()
    return row.name if row else None


def evaluate_case_customer(case: EvalCase, db: Any) -> EvalResult:
    """Run one customer eval case through the live matcher.

    Checks only the primary customer_id match — not the alternatives list.
    For cases with expected_customer_name=None (ambiguous/negative), a
    correct result is customer_id=None. For cases with an expected name,
    the returned customer_id must resolve to that canonical name.

    Parameters:
        case: The EvalCase to evaluate (must be a customer category).
        db: SQLAlchemy Session backed by a seeded test DB with search entries.

    Returns:
        EvalResult with passed=True iff the primary match is correct.

    Side effects:
        * DB reads (Stage A + name lookup).
        * One OpenAI API call (Stage B) unless cached.
    """
    from providers import semantic_matcher

    try:
        ctx = _build_conversation_context(case.context, db)
        match = semantic_matcher.match_customer(
            case.input,
            conversation_ctx=ctx,
            conversation_id=uuid.uuid4(),
            db=db,
        )

        actual_name: str | None = None
        if match.customer_id is not None:
            actual_name = _get_customer_name_by_id(match.customer_id, db)

        if case.expected_customer_name is None:
            # Expect no match (negative/ambiguous/collision)
            passed = match.customer_id is None
        else:
            passed = actual_name == case.expected_customer_name

        return EvalResult(
            case=case,
            passed=passed,
            actual_customer_name=actual_name,
        )
    except Exception as exc:
        return EvalResult(
            case=case,
            passed=False,
            error=str(exc),
        )


def evaluate_case_sku(case: EvalCase, db: Any) -> EvalResult:
    """Run one SKU eval case through the live matcher.

    Checks only the primary sku_kind match — not the alternatives list.
    For sku_ambiguous cases with expected_sku_kind=None, a correct result
    is sku_kind=None. For other cases, the returned sku_code must match
    expected_sku_code (when specified) and sku_kind must match.

    Parameters:
        case: The EvalCase to evaluate (must be a SKU category).
        db: SQLAlchemy Session backed by a seeded test DB with search entries.

    Returns:
        EvalResult with passed=True iff the primary match is correct.

    Side effects:
        * DB reads (Stage A + SKU lookup).
        * One OpenAI API call (Stage B) unless cached.
    """
    from agents.schemas import SkuContext
    from providers import semantic_matcher

    try:
        match = semantic_matcher.match_sku(
            case.input,
            sku_context=SkuContext(),
            conversation_id=uuid.uuid4(),
            db=db,
        )

        actual_sku_kind = match.sku_kind
        actual_sku_code = match.sku_code

        if case.expected_sku_kind is None:
            # Expect no match (sku_ambiguous / sku_negative)
            passed = match.sku_kind is None
        else:
            kind_ok = actual_sku_kind == case.expected_sku_kind
            if case.expected_sku_code is not None:
                code_ok = actual_sku_code == case.expected_sku_code
            else:
                code_ok = True
            passed = kind_ok and code_ok

        return EvalResult(
            case=case,
            passed=passed,
            actual_sku_kind=actual_sku_kind,
            actual_sku_code=actual_sku_code,
        )
    except Exception as exc:
        return EvalResult(
            case=case,
            passed=False,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def corpus_sha(path: Path = CORPUS_PATH) -> str:
    """Return the SHA-256 hex digest of the corpus YAML file.

    Parameters:
        path: Path to matcher_corpus.yaml.

    Returns:
        Hex string (64 chars).

    Side effects: reads the file from disk.
    """
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest()


def resolve_git_sha() -> str:
    """Return the current HEAD git SHA (short, 8 chars).

    Returns 'unknown' when git is not available or the working directory
    is not a git repo.

    Side effects: spawns a git subprocess.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=8", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def write_report(
    name: str,
    category_results: list[CategoryResult],
    thresholds: dict[str, float],
    *,
    eval_mode: str = EVAL_MODE,
    git_sha: str | None = None,
    corpus_sha_hex: str | None = None,
) -> Path:
    """Write a Markdown eval report to .reports/<name>.md.

    Creates REPORTS_DIR if it does not exist. Report is always written
    even when some categories fail — the report documents the failure.

    Parameters:
        name: Report base name (e.g. 'matcher_eval_customer').
        category_results: List of CategoryResult objects, one per category.
        thresholds: Per-category precision thresholds dict.
        eval_mode: 'sample' or 'full'.
        git_sha: Current HEAD SHA for versioning. Auto-resolved if None.
        corpus_sha_hex: SHA of corpus YAML. Auto-computed if None.

    Returns:
        Path to the written report file.

    Side effects:
        Creates .reports/ directory if absent. Writes .reports/<name>.md.
    """
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"{name}.md"

    git_sha = git_sha or resolve_git_sha()
    corpus_sha_hex = corpus_sha_hex or corpus_sha()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    total_cases = sum(r.total for r in category_results)
    total_passed = sum(r.passed for r in category_results)
    overall_precision = total_passed / total_cases if total_cases else 0.0

    all_pass = all(
        r.precision >= thresholds.get(r.category, 1.0)
        for r in category_results
    )

    lines: list[str] = [
        f"# Eval Report: {name}",
        f"",
        f"**Generated:** {now}  ",
        f"**Git SHA:** `{git_sha}`  ",
        f"**Corpus SHA:** `{corpus_sha_hex[:12]}`  ",
        f"**Eval mode:** `{eval_mode}`  ",
        f"**Result:** {'ALL PASS' if all_pass else 'FAILURES PRESENT'}  ",
        f"",
        f"Overall precision: **{overall_precision:.1%}** ({total_passed}/{total_cases})",
        f"",
        f"## Per-Category Results",
        f"",
        f"| Category | Precision | Pass/Total | Threshold | Status |",
        f"|---|---|---|---|---|",
    ]

    for r in sorted(category_results, key=lambda x: x.category):
        threshold = thresholds.get(r.category, 1.0)
        status = "PASS" if r.precision >= threshold else "FAIL"
        lines.append(
            f"| `{r.category}` "
            f"| {r.precision:.1%} "
            f"| {r.passed}/{r.total} "
            f"| {threshold:.0%} "
            f"| {status} |"
        )

    # Failures section
    failures_exist = any(r.failures for r in category_results)
    if failures_exist:
        lines += [
            f"",
            f"## Failure Details",
            f"",
        ]
        for r in sorted(category_results, key=lambda x: x.category):
            if not r.failures:
                continue
            lines += [
                f"### `{r.category}` failures ({len(r.failures)})",
                f"",
                f"| Input | Expected | Got | Error |",
                f"|---|---|---|---|",
            ]
            for res in r.failures:
                case = res.case
                if case.is_sku_case:
                    expected = f"kind={case.expected_sku_kind!r} code={case.expected_sku_code!r}"
                    got = f"kind={res.actual_sku_kind!r} code={res.actual_sku_code!r}"
                else:
                    expected = repr(case.expected_customer_name)
                    got = repr(res.actual_customer_name)
                error_cell = (res.error or "").replace("|", "\\|")[:80]
                input_escaped = case.input.replace("|", "\\|")
                lines.append(f"| `{input_escaped}` | {expected} | {got} | {error_cell} |")
            lines.append("")

    report_path.write_text("\n".join(lines) + "\n")
    return report_path


# ---------------------------------------------------------------------------
# Aggregation helper
# ---------------------------------------------------------------------------


def aggregate_results(results: list[EvalResult]) -> list[CategoryResult]:
    """Aggregate a flat list of EvalResult into CategoryResult per category.

    Parameters:
        results: All EvalResult objects from evaluating a set of cases.

    Returns:
        List of CategoryResult, one per category seen in the results.

    Side effects: none.
    """
    by_category: dict[str, CategoryResult] = {}
    for r in results:
        cat = r.case.category
        if cat not in by_category:
            by_category[cat] = CategoryResult(category=cat, total=0, passed=0)
        by_category[cat].total += 1
        if r.passed:
            by_category[cat].passed += 1
        else:
            by_category[cat].failures.append(r)
    return list(by_category.values())
