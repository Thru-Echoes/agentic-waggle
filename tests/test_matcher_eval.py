"""Eval harness: run matcher_corpus.yaml cases against the live matcher.

Two tests:
    test_matcher_eval_customer  -- all customer-category cases
    test_matcher_eval_sku       -- all SKU-category cases

Both tests:
  - Require OPENAI_API_KEY (real LLM calls).
  - Require TEST_DATABASE_URL (real Postgres with pgvector).
  - Use db_session_with_search_entries (seeded DB + backfill_all called).
  - Write a Markdown report to .reports/ BEFORE asserting thresholds so
    the report exists even when some categories fail.

Run in sample mode (default, 20% stratified):
    TEST_DATABASE_URL=... pytest tests/test_matcher_eval.py -v

Run in full mode:
    MATCHER_EVAL_FULL=1 TEST_DATABASE_URL=... pytest tests/test_matcher_eval.py -v

Or equivalently via MATCHER_EVAL_MODE=full:
    MATCHER_EVAL_MODE=full TEST_DATABASE_URL=... pytest tests/test_matcher_eval.py -v
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import os

import pytest
from sqlalchemy.orm import Session

from tests._eval_helpers import (
    CUSTOMER_THRESHOLDS,
    EVAL_MODE,
    REPORTS_DIR,
    SKU_THRESHOLDS,
    CategoryResult,
    EvalCase,
    EvalResult,
    aggregate_results,
    corpus_sha,
    evaluate_case_customer,
    evaluate_case_sku,
    load_corpus,
    resolve_git_sha,
    stratified_sample,
    write_report,
)

# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------

require_openai_key = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set — eval tests require a real LLM",
)


def _select_cases(cases: list[EvalCase]) -> list[EvalCase]:
    """Return full corpus or stratified sample depending on MATCHER_EVAL_MODE.

    Parameters:
        cases: Full corpus loaded from YAML.

    Returns:
        Selected subset (or full list) of EvalCase objects.

    Side effects: none.
    """
    full_mode = (
        os.environ.get("MATCHER_EVAL_FULL") == "1"
        or os.environ.get("MATCHER_EVAL_MODE", "sample") == "full"
    )
    if full_mode:
        return cases
    return stratified_sample(cases)


# ---------------------------------------------------------------------------
# Customer eval test
# ---------------------------------------------------------------------------


@require_openai_key
def test_matcher_eval_customer(db_session_with_search_entries: Session) -> None:
    """Run all customer-category corpus cases and assert per-category thresholds.

    Writes .reports/matcher_eval_customer.md before asserting so the report
    exists even when some categories fail.

    Threshold table (CUSTOMER_THRESHOLDS):
        canonical: 1.00, abbreviation: 0.98, possessive: 0.98,
        alias: 0.98, apostrophe_variant: 0.98, site_reference: 0.95,
        site_shortform: 0.95, typo_resolvable: 0.90, long_form_sentence: 0.95,
        pronoun_resolution: 0.85, cross_customer_collision: 0.90,
        ambiguous_reference: 0.95, negative_case: 1.00, generic_words: 1.00,
        compound_reference: 0.85, casual_colloquial: 0.95, partial_match_noise: 0.95
    """
    db = db_session_with_search_entries

    all_cases = load_corpus()
    customer_cases = [c for c in all_cases if not c.is_sku_case]
    selected = _select_cases(customer_cases)

    eval_mode = (
        "full"
        if (
            os.environ.get("MATCHER_EVAL_FULL") == "1"
            or os.environ.get("MATCHER_EVAL_MODE", "sample") == "full"
        )
        else EVAL_MODE
    )

    results: list[EvalResult] = []
    for case in selected:
        result = evaluate_case_customer(case, db)
        results.append(result)

    category_results: list[CategoryResult] = aggregate_results(results)

    git_sha = resolve_git_sha()
    corpus_sha_hex = corpus_sha()

    report_path = write_report(
        "matcher_eval_customer",
        category_results,
        CUSTOMER_THRESHOLDS,
        eval_mode=eval_mode,
        git_sha=git_sha,
        corpus_sha_hex=corpus_sha_hex,
    )
    print(f"\nReport written: {report_path}")

    # Threshold assertions — after report is written
    failures: list[str] = []
    for cat_result in sorted(category_results, key=lambda r: r.category):
        threshold = CUSTOMER_THRESHOLDS.get(cat_result.category, 1.0)
        if cat_result.precision < threshold:
            failures.append(
                f"  {cat_result.category}: precision={cat_result.precision:.1%} "
                f"< threshold={threshold:.0%} "
                f"({cat_result.passed}/{cat_result.total} passed)"
            )

    if failures:
        failure_lines = "\n".join(failures)
        report_rel = REPORTS_DIR / "matcher_eval_customer.md"
        raise AssertionError(
            f"Customer eval thresholds not met:\n{failure_lines}\n"
            f"\nSee full report at {report_rel}"
        )


# ---------------------------------------------------------------------------
# SKU eval test
# ---------------------------------------------------------------------------


@require_openai_key
def test_matcher_eval_sku(db_session_with_search_entries: Session) -> None:
    """Run all SKU-category corpus cases and assert per-category thresholds.

    Writes .reports/matcher_eval_sku.md before asserting so the report
    exists even when some categories fail.

    Threshold table (SKU_THRESHOLDS):
        sku_canonical_code: 1.00, sku_product_name: 0.98,
        sku_qty_plus_product: 0.98, sku_service: 0.95,
        sku_colloquial: 0.90, sku_ambiguous: 0.95, sku_negative: 1.00
    """
    db = db_session_with_search_entries

    # SKU test requires products + services in the DB.
    # The conftest db_session_with_search_entries seeds customers/regions/sites
    # and calls backfill_all, but does NOT seed products or services.
    # We insert the seed product+service data for SKU matching.
    _seed_sku_data(db)

    all_cases = load_corpus()
    sku_cases = [c for c in all_cases if c.is_sku_case]
    selected = _select_cases(sku_cases)

    eval_mode = (
        "full"
        if (
            os.environ.get("MATCHER_EVAL_FULL") == "1"
            or os.environ.get("MATCHER_EVAL_MODE", "sample") == "full"
        )
        else EVAL_MODE
    )

    results: list[EvalResult] = []
    for case in selected:
        result = evaluate_case_sku(case, db)
        results.append(result)

    category_results: list[CategoryResult] = aggregate_results(results)

    git_sha = resolve_git_sha()
    corpus_sha_hex = corpus_sha()

    report_path = write_report(
        "matcher_eval_sku",
        category_results,
        SKU_THRESHOLDS,
        eval_mode=eval_mode,
        git_sha=git_sha,
        corpus_sha_hex=corpus_sha_hex,
    )
    print(f"\nReport written: {report_path}")

    # Threshold assertions — after report is written
    failures: list[str] = []
    for cat_result in sorted(category_results, key=lambda r: r.category):
        threshold = SKU_THRESHOLDS.get(cat_result.category, 1.0)
        if cat_result.precision < threshold:
            failures.append(
                f"  {cat_result.category}: precision={cat_result.precision:.1%} "
                f"< threshold={threshold:.0%} "
                f"({cat_result.passed}/{cat_result.total} passed)"
            )

    if failures:
        failure_lines = "\n".join(failures)
        report_rel = REPORTS_DIR / "matcher_eval_sku.md"
        raise AssertionError(
            f"SKU eval thresholds not met:\n{failure_lines}\n"
            f"\nSee full report at {report_rel}"
        )


# ---------------------------------------------------------------------------
# SKU seeder helper (products + services needed by the eval)
# ---------------------------------------------------------------------------


def _seed_sku_data(db: Session) -> None:
    """Seed products and services into the test DB, then rebuild sku_search_entries.

    The seed generators (seed.products, seed.services) require Faker and
    produce the canonical product/service rows the corpus references. This
    helper calls them directly using the test session, then re-runs backfill_all
    so sku_search_entries is populated.

    Side effects:
        - INSERTs into products, services, product_regulatory_items.
        - Re-runs providers.semantic_matcher.backfill_all (TRUNCATE + INSERT).
        - Commits.

    Parameters:
        db: SQLAlchemy Session from db_session_with_search_entries.
    """
    from sqlalchemy import text

    from providers import semantic_matcher

    # Check if products are already seeded (idempotent)
    count = db.execute(text("SELECT COUNT(*) FROM products")).scalar_one()
    if count > 0:
        # Already seeded — just ensure sku_search_entries is populated
        sku_count = db.execute(text("SELECT COUNT(*) FROM sku_search_entries")).scalar_one()
        if sku_count == 0:
            semantic_matcher.backfill_all(db)
        return

    # Run the seed generators
    from faker import Faker

    from seed import (
        products as products_gen,
        services as services_gen,
    )
    from seed.faker_providers import BeekeepingProvider

    faker = Faker(locale="en_US")
    Faker.seed(42)
    faker.add_provider(BeekeepingProvider)

    products_list = products_gen.generate(faker)
    products_gen.persist(db, products_list)

    services_list = services_gen.generate()
    services_gen.persist(db, services_list)

    db.commit()

    # Re-run backfill to populate sku_search_entries (and refresh customer entries)
    semantic_matcher.backfill_all(db)
