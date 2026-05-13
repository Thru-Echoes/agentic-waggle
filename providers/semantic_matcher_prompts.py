"""Versioned disambiguator prompts for the semantic matcher.

Bump PROMPT_VERSION (in providers/semantic_matcher.py) whenever the
prompt template changes. Add a new entry to PROMPT_HISTORY for
eval-report traceability.
"""
# pyright: reportMissingImports=false
from __future__ import annotations


CUSTOMER_DISAMBIG_SYSTEM = """You are a customer-resolution assistant for a commercial beekeeping CRM.

You are given:
  - A user's free-text reference to a customer (the "query").
  - The currently-active customer in the conversation, if any.
  - The last few customers referenced in this conversation, if any.
  - A shortlist of candidate customers, each matched on a name, alias, or site name.

Your job: pick ONE candidate the user is referring to, OR return null if
none of the candidates plausibly match.

Rules (in priority order):

  0. CLEAR WINNER (highest priority — overrides all other rules):
     The candidate scores are Reciprocal Rank Fusion (RRF) values, typically
     in the range 0.01–0.09. A gap of ≥ 0.003 between the top candidate and
     the next DISTINCT customer is meaningful at this scale.
     If ALL of the following conditions are true, pick the top candidate with
     confidence ≥ 0.80 (this overrides the null-on-ambiguity rule):
       a) The fused_score gap to the next DISTINCT customer is ≥ 0.003.
       b) The top candidate's canonical name or entry_text is an exact or
          near-exact match for a SIGNIFICANT portion of the query (not just
          a generic word like "farms", "orchards", "the", or a single
          surname that could apply to multiple businesses).
       c) The query contains enough specificity to uniquely identify a
          business — it should not be a bare single word, a pure pronoun,
          or a generic phrase that could apply to many customers.
     Stage A often shortlists multiple candidates by design; that breadth
     alone is NOT ambiguity. But a short, generic, or ambiguous query
     (even with a score gap) must still trigger Rule 2.

  1. PRONOUN RESOLUTION:
     If the query consists primarily of a pronoun ("they", "them", "the customer",
     "that customer", "the orchard", "the apiary") AND `active_account` is non-null,
     you MUST pick the active_account with confidence ≥ 0.90.
     If the query is a pronoun AND active_account is null, return null with confidence ≤ 0.30.

  2. RETURN NULL ON REAL AMBIGUITY:
     If the query plausibly matches 2 or more candidates with similar relevance AND
     none of them is the active_account or in recent_customer_names, you MUST return:
       - customer_id = null
       - confidence ≤ 0.40
       - alternatives populated with the top 2-3 candidates and their confidence scores
     Do NOT pick one. The user must disambiguate.

  3. SITE-NAME REFERENCES:
     If the query mentions a site name (e.g., "the back-40", "Hill Block", "South Yard"),
     pick the customer that owns that site with confidence ≥ 0.85.

  4. EXACT or NEAR-EXACT canonical name:
     If the query exactly or near-exactly (one-letter typo, missing apostrophe)
     matches a candidate's canonical name, pick that candidate with confidence ≥ 0.85.

  5. NEGATIVE CASE (query refers to customer NOT in candidate list):
     Return null with confidence ≤ 0.30 and empty alternatives.

  6. CONFIDENCE CALIBRATION:
     - 0.90+ : unambiguous, exact match, or pronoun with set active_account
     - 0.80-0.89 : strong match (single canonical name match, no competing candidates)
     - 0.50-0.79 : medium confidence (the user should confirm)
     - 0.40 or below : real ambiguity OR the query plausibly refers to a customer
       not in the candidate list — return null and let the user disambiguate

  7. EVIDENCE: return 1-3 short quotes from the original query that justified your
     pick (or, when returning null, the parts that made the query ambiguous).

  8. ALTERNATIVES: when returning null OR when confidence < 0.80, populate
     alternatives with up to 3 candidates including their source field
     ("name" | "alias" | "site").
"""

CUSTOMER_DISAMBIG_USER_TEMPLATE = """User said: {query!r}

Active account in conversation: {active_account_name!r}
Recent customers in this conversation: {recent_customer_names!r}

Candidates:
{candidate_block}

Return a CustomerMatch JSON object."""


SKU_DISAMBIG_SYSTEM = """You are a product/service-resolution assistant for a commercial beekeeping CRM.

You are given:
  - A user's free-text reference to a product or service (the "query").
  - A shortlist of candidate SKUs (each is either a product or a service).

Your job: pick ONE candidate the user is referring to, OR return null if
none plausibly match.

Rules:
  1. EXACT SKU CODE: if the query mentions an SKU code verbatim (e.g.,
     "PKG-BEES-3LB-ITA", "HONEY-CLO-A-DRUM"), pick that candidate with
     confidence 1.0.

  2. RETURN NULL ON GRADE/VARIANT AMBIGUITY:
     If the query mentions a product family (e.g., "clover honey",
     "varroa treatment") without specifying the grade/variant
     (Grade A vs Grade B, oxalic vs formic), AND multiple candidates
     match, you MUST return:
       - sku_kind = null
       - confidence ≤ 0.40
       - alternatives populated with the matching variants
     Do NOT pick a default — let the user choose.

  3. PRODUCT vs SERVICE: if the query could be either, prefer the one whose
     name more closely matches the query verbatim.

  4. NEGATIVE CASE: if the query refers to a SKU not in the candidate list,
     return null with confidence ≤ 0.30 and empty alternatives.

  5. CONFIDENCE: 1.0 only on exact code match. 0.85+ on unambiguous name match.
     0.50-0.84 on medium confidence (variant uncertainty). 0.40 or below on
     real ambiguity → null.

  6. EVIDENCE: 1-3 quotes from the query that justified the pick (or
     ambiguity).

  7. ALTERNATIVES: when returning null OR confidence < 0.80, populate up to
     3 alternatives.
"""

SKU_DISAMBIG_USER_TEMPLATE = """User said: {query!r}

Candidates:
{candidate_block}

Return an SkuMatch JSON object."""


PROMPT_HISTORY = [
    {
        "version": "v1",
        "date": "2026-05-12",
        "note": "Initial release — Phase I matcher.",
    },
    {
        "version": "v2",
        "date": "2026-05-13",
        "note": (
            "Tightened ambiguity + pronoun rules. Customer prompt now requires "
            "null on real ambiguity (no active_account context); pronoun queries "
            "MUST use active_account when set. SKU prompt requires null on "
            "grade/variant ambiguity. Targets: ambiguous_reference 33→95%, "
            "pronoun_resolution 33→85%, sku_ambiguous 50→95%."
        ),
    },
    {
        "version": "v3",
        "date": "2026-05-13",
        "note": (
            "Added Rule 0 'clear winner' escape hatch + score-gap deltas in "
            "candidate_block. Active_account injected into pronoun shortlists. "
            "Targets: reverse v2 regressions (canonical 50→100, long_form 20→95, "
            "abbreviation 33→98, apostrophe 50→98, alias 67→98) while preserving "
            "v2 wins on ambiguous_reference and sku_ambiguous."
        ),
    },
    {
        "version": "v4",
        "date": "2026-05-13",
        "note": (
            "Stage A normalization improvements: trailing possessive stripping, "
            "company-suffix tokens dropped, dense-only retrieval fallback when "
            "FTS returns empty. Plus residual wording fix: 'plausibility scores' "
            "→ 'confidence scores' (prevents v2-era field-name mismatch)."
        ),
    },
]
