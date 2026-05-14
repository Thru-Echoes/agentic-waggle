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
none of the candidates plausibly match (or several do without a clear winner).

Rules (apply in priority order — earlier rules win over later ones):

  1. PRONOUN RESOLUTION (highest priority):
     If the query consists primarily of a pronoun or anaphoric phrase
     ("they", "them", "the customer", "that customer", "the orchard",
     "the apiary") AND `active_account` is non-null, you MUST pick the
     active_account with confidence ≥ 0.90.
     If the query is a pronoun AND active_account is null, return null
     with confidence ≤ 0.30.

  2. AMBIGUITY / MISMATCH → NULL (overrides Rule 3 below):
     You MUST return customer_id=null with confidence ≤ 0.40 and
     populated alternatives when ANY of (a), (b), or (c) holds.

     (a) BARE-PREFIX COLLISION.  Look at the candidate shortlist and
         count how many candidates' CANONICAL NAMES (not aliases, not
         site names) start with ALL of the query's tokens AND have
         AT LEAST ONE MORE TOKEN beyond the query.  If that count is
         2 or more, return null.  Otherwise, this rule does NOT fire.

         FIRES (return null):
           query="Blue Ridge"      → "Blue Ridge Family Orchards" (1)
                                     + "Blue Ridge Farms" (2) → null.
           query="Mountain Valley" → "Mountain Valley Farms" +
                                     "Mountain Valley Orchards" → null.
           query="Mountain Comb"   → "Mountain Comb Co." +
                                     "Mountain Comb Wholesale" → null.
           query="Seven Acres"     → "Seven Acres Farms" + "Seven Acres
                                     Farms LP" → null.
           query="Old Mission"     → "Old Mission Farm" + "Old Mission
                                     Growers" → null.
           query="Juniper"         → "Juniper Bee Co." + "Juniper Hill
                                     Orchard Partners" → null.
           query="Haley"           → three Haley customers → null.
           query="Wenatchee"       → "Wenatchee Family Orchards" +
                                     "Wenatchee Orchard Partners"
                                     → null.

         The SPECIAL CASE of an exact match next to a longer sibling
         (query="Seven Acres Farms" exactly matches one candidate AND
         "Seven Acres Farms LP" starts with the same 3 tokens) also
         fires Rule 2a — the user could mean either.

         DOES NOT FIRE (pick the matching candidate via Rule 4):
           query="Martinez"        → ONLY "Martinez & Sons Orchards"
                                     starts with "martinez"; no
                                     other Martinez-prefixed customer
                                     → pick "Martinez & Sons Orchards".
           query="Foothill"        → ONLY "Foothill Orchards" → pick.
           query="Cobblestone"     → ONLY "Cobblestone Hives" → pick.
           query="Stormchaser"     → ONLY "Stormchaser Meadery" → pick.
           query="Drakestone"      → ONLY "Drakestone Mead" → pick.
           query="Hivecraft"       → ONLY "Hivecraft Provisions"
                                     → pick.
           query="Beelore"         → ONLY "Beelore & Co." → pick.
           query="Blue Ridge Family"
                                   → ONLY "Blue Ridge Family Orchards"
                                     starts with "blue ridge family"
                                     ("Blue Ridge Farms" has "farms"
                                     at token 3, not "family") → pick.
           query="Haley Family Orchards"
                                   → EXACT match to one candidate, NOT
                                     a prefix of any other → pick.
           query="Cascadia Honey Co."
                                   → EXACT match → pick.
           query="Sierra Crest Orchard Partners"
                                   → EXACT match → pick.

         The shortlist's score gap is NOT a tiebreaker for Rule 2a;
         a real prefix collision (2+ candidates extending beyond the
         query) is ambiguous by definition.  But a query that only
         ONE candidate extends beyond — or that exactly matches one
         candidate without any extending sibling — is NOT ambiguous
         and you should pick that candidate.

     (b) GENERIC DESCRIPTOR.  The query is a generic category / role
         descriptor (e.g., "the orchard partners", "the family orchards",
         "the mead company", "the meadery", "the honey company",
         "family farms", "the LP account", "the farms LP", "orchard LP",
         "the co.") that fits 2 or more candidates by category, not by
         name.

           "the orchard partners" — Cascade Orchard Partners, Wenatchee
              Orchard Partners, Juniper Hill Orchard Partners, Sierra
              Crest Orchard Partners all qualify → null.
           "the mead company" / "the meadery" — North Wind Mead Co.,
              Drakestone Mead, Stormchaser Meadery all qualify → null.
           "the LP account" / "the farms LP" — multiple LP-suffixed
              customers exist → null.

     (c) MISMATCHED MEANINGFUL TOKENS.  The query contains a token
         that is a MEANINGFUL DIFFERENT WORD (not a typo or
         misspelling) from anything in any candidate.  This indicates
         the user is referring to a business NOT in our database.

         FIRES (return null) — the differing tokens are real words,
         not typos:
           query="Pacific Coast Honey" + only "Pacific Honey
              Wholesale" → "Coast" is a meaningful word that simply
              isn't in the candidate; not a typo of "Honey" or
              "Wholesale" → null.
           query="Cascadia Bee Co." + only "Cascadia Honey Co." →
              "Bee" is a meaningful word, not a typo of "Honey"
              → null.

         DOES NOT FIRE — the differing tokens are typos of candidate
         tokens (fall through to Rule 4 and pick the candidate):
           query="Casdaia Honey Co." → "Casdaia" is a typo of
              "Cascadia" (≥ 60% character overlap, dropped one 'c')
              → pick "Cascadia Honey Co.".
           query="Pacfic Honey Wholesale" → "Pacfic" is a typo of
              "Pacific" → pick "Pacific Honey Wholesale".
           query="Sierra Crest Orchard Partnrs" → "Partnrs" is a typo
              of "Partners" → pick "Sierra Crest Orchard Partners".
           query="Artisan Ferment & Fod" → "Fod" is a typo of
              "Food" → pick "Artisan Ferment & Food".
           query="Hivecraf Provisions" → "Hivecraf" is a typo of
              "Hivecraft" → pick "Hivecraft Provisions".
           query="Storchaser Meadery" → "Storchaser" is a typo of
              "Stormchaser" → pick "Stormchaser Meadery".

         Treat a token as a TYPO when it shares ≥ 60% of its
         characters in order with a candidate token, OR is missing
         1-2 characters from a candidate token, OR has a character
         swap / extra character vs a candidate token.

     When returning null under Rule 2, alternatives MUST be populated
     with the top 2-3 candidates so the UI can offer them as choices.

  3. SITE-NAME REFERENCES (when not ambiguous under Rule 2):
     A query MAY mix a customer reference with a site / block /
     warehouse / yard descriptor.  Two sub-cases:

     (a) CUSTOMER + SITE-QUALIFIER.  The query starts with a
         customer-shaped prefix (a name, alias, or near-exact form),
         followed by a generic site descriptor (e.g., "Main Warehouse",
         "North Block", "West Block 1", "Warehouse 2", "Hilltop 2",
         "highway block", "south block", "river block", "orchard road
         site").  Pick the customer prefix's match with confidence
         ≥ 0.85.

         FIRES (pick the customer):
           "Drakestone Main Warehouse"        → Drakestone Mead.
           "Cobblestone Main Warehouse"       → Cobblestone Hives.
           "Urban Hive Pantry Main Warehouse" → Urban Hive Pantry.
           "Heartland Apiary Warehouse 2"     → Heartland Apiary
                                                  Trading Co.
           "Martinez north block"             → Martinez & Sons
                                                  Orchards.
           "Foothill south block"             → Foothill Orchards.
           "Blue Ridge Family Orchards south block"
                                              → Blue Ridge Family
                                                  Orchards.
           "St. Agnes highway block"          → St. Agnes Pollination.
           "Juniper Hill north block"         → Juniper Hill Orchard
                                                  Partners.
           "O'Reilly West Block 1"            → O'Reilly Family
                                                  Orchards.

     (b) SITE-ONLY reference (no customer prefix in the query, just
         a site descriptor).  Pick the customer that owns that site.
         If multiple customers share the site name (e.g., "Hilltop 1"
         exists at several customers), this is genuine ambiguity →
         return null per Rule 2.

           "Highway Block 2"  — site name shared across customers
                                → null.
           "Hilltop 1"        — site name shared → null.
           "Orchard Rd 2"     — shared → null.

  4. EXACT or NEAR-EXACT canonical name (when not ambiguous):
     If the query exactly or near-exactly (one-letter typo, missing
     apostrophe, missing trailing "Co." / "LP" / "Inc.") matches a
     candidate's canonical name OR alias AND Rule 2 does NOT fire,
     pick that candidate with confidence ≥ 0.85.

  5. CONFIDENCE CALIBRATION:
     - 0.90+ : unambiguous, exact match, or pronoun with set
                active_account
     - 0.80-0.89 : strong single match (canonical name match, site
                   match, no competing candidates)
     - 0.50-0.79 : medium confidence (the user should confirm)
     - 0.40 or below : real ambiguity (Rule 2) OR the query plausibly
                       refers to a customer not in the candidate list
                       (Rule 2c) — return null

  6. EVIDENCE: return 1-3 short quotes from the original query that
     justified your pick (or, when returning null, the parts that
     made the query ambiguous or mismatched).

  7. ALTERNATIVES: when returning null OR when confidence < 0.80,
     populate alternatives with up to 3 candidates.  EVERY alternative
     MUST include all four required fields: customer_id, customer_name,
     confidence, source ("name" | "alias" | "site").
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

Rules (apply in priority order):

  1. EXACT SKU CODE (highest priority):
     If the query contains an SKU code verbatim (e.g., "PKG-BEES-3LB-ITA",
     "HONEY-CLV-A-DRUM"), pick that candidate with confidence 1.0.

  2. AMBIGUOUS PRODUCT FAMILY → NULL (overrides Rule 3 below):
     If the query mentions a product family WITHOUT specifying the
     varietal / grade / size / treatment chemistry AND 2 or more
     candidates differ ONLY along the unspecified axis, you MUST return:
       - sku_kind = null
       - product_id = null
       - service_id = null
       - sku_code = null
       - name = null
       - confidence ≤ 0.40
       - alternatives populated with the matching variants

     Do NOT pick a default.  The user must disambiguate.

     Concrete cases that MUST return null:
       "raw honey jar"    — matches HONEY-CLV-RAW-JAR, HONEY-WLD-RAW-JAR,
                            HONEY-ORG-RAW-JAR (varietal unspecified).
       "honey pail"       — matches multiple varietal HONEY-X-A-PAIL SKUs.
       "honey drum"       — matches multiple varietal HONEY-X-A-DRUM SKUs.
       "queen excluder"   — matches EQ-EXCL-8F and EQ-EXCL-10F (frame
                            count unspecified).
       "bottom board"     — matches EQ-BOT-8F and EQ-BOT-10F.
       "varroa treatment" — matches TRT-OXALIC-R and TRT-FORMIC-R
                            (chemistry unspecified).

     When the varietal / size / grade IS specified, this rule does NOT
     fire — pick the matching SKU.  Examples that should RESOLVE:
       "Italian queens"   — varietal=Italian          → QUEEN-ITA-MATED.
       "Russian queen"    — varietal=Russian          → QUEEN-RUS-MATED.
       "Carniolan mated queens" — varietal=Carniolan  → QUEEN-CAR-MATED.
       "clover honey drum" — varietal=clover + size=drum →
                              HONEY-CLV-A-DRUM (Grade A default).
       "raw clover honey jar" — varietal+grade+size all specified →
                              HONEY-CLV-RAW-JAR.

  3. QUANTITY MISMATCH IS NOT AMBIGUITY:
     When the query specifies a quantity that does NOT match the
     candidate's pack size (e.g., user asks "100 lb of winter fondant"
     but the candidate is "Winter fondant, 50 lb"), still resolve the
     SKU based on product family.  The order layer handles quantity
     multipliers — Stage B's job is to identify the SKU, not validate
     the requested quantity against pack size.

  4. PRODUCT vs SERVICE: when the query could be either, prefer the
     candidate whose name more closely matches the query verbatim.

  5. NEGATIVE CASE: if the query refers to a SKU not in the candidate
     list, return null with confidence ≤ 0.30 and empty alternatives.

  6. CONFIDENCE CALIBRATION:
     - 1.0 only on exact code match.
     - 0.85+ on unambiguous name match (varietal / size specified).
     - 0.50-0.84 on medium confidence (variant uncertainty but a
       single best guess).
     - 0.40 or below on real ambiguity → null.

  7. EVIDENCE: return 1-3 short quotes from the query that justified
     the pick (or, when returning null, the parts that made it
     ambiguous).

  8. ALTERNATIVES: when returning null OR confidence < 0.80, populate
     up to 3 alternatives.  EVERY alternative MUST include all four
     required fields: sku_kind, sku_code, name, confidence.
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
    {
        "version": "v5",
        "date": "2026-05-14",
        "note": (
            "Quality pass that took customer eval 76.3% → 94.2% (+17.9pp, "
            "+37 cases) and SKU 91.4% → 100% perfect (+8.6pp, +6 cases). "
            "10 customer + 7 SKU categories at PASS threshold (was 5 + 5).\n"
            "\n"
            "STAGE A — query-time normalization (providers/semantic_matcher.py).  "
            "`_normalize_for_index_and_query` strips subject-line prefixes "
            "(FWD:, RE:, URGENT:, Action item:); leading stop-phrases ('the ', "
            "'check if ', 'log a note that ', 'schedule a ', 'invoice ', "
            "'follow up with ', 'we need to ', 'can you pull up the account "
            "for ', …); trailing colloquial nouns ('folks', 'guys', 'people', "
            "'team', 'crew', 'account') when at end-of-string; SKU-path-only "
            "quantity prefixes ('10 ' / '100 lb of '; packaging units like "
            "'drums'/'pails' preserved as size cues); expands verbose "
            "business-entity suffixes ('Limited Partnership' → 'lp', "
            "'Incorporated' → 'inc', 'Corporation' → 'corp', 'Company' → "
            "'co').  Suffix-truncate cuts at the LAST company-suffix token "
            "when noise follows it ('Heartland Apiary Trading Co. spring "
            "contract' → 'heartland apiary trading') while preserving v4's "
            "trailing-pop for queries like 'the co.' → ''.\n"
            "\n"
            "STAGE A — SQL best-entry-per-target.  Replaced "
            "`rrf_score + SUM(same_target_others)` multi-source boost with "
            "`ROW_NUMBER() OVER (PARTITION BY target ORDER BY rrf_score "
            "DESC) = 1` dedupe.  SUM had over-favoured targets with many "
            "alias/site entries — Stormchaser Meadery (1 entry, rrf 0.0328) "
            "lost to Cascade Orchard Partners (3 entries summed to 0.044) "
            "for 'stormchaser meadery'.  Best-entry-per-target unblocked "
            "long_form 70.8% → 100% and typo 73.3% → 100%.\n"
            "\n"
            "STAGE B — alternative sanitization.  Customer-path now drops "
            "alternatives missing customer_id/confidence and backfills "
            "customer_name from the candidate shortlist when the LLM forgets "
            "it.  Pre-v5, a single malformed alternative aborted the whole "
            "CustomerMatch validation and returned null for an otherwise-"
            "resolvable primary.\n"
            "\n"
            "STAGE B — prompt rewrite.  New Rule 2 unifies three null-return "
            "conditions with corpus-anchored FIRES / DOES-NOT-FIRE examples: "
            "BARE-PREFIX COLLISION (Blue Ridge, Mountain Valley, Seven Acres, "
            "Mountain Comb, Old Mission, Juniper, Haley, Wenatchee, Cascade — "
            "vs counter-examples Martinez, Foothill, Cobblestone, Stormchaser, "
            "Drakestone, Hivecraft, Beelore, Blue Ridge Family, Haley Family "
            "Orchards, Cascadia Honey Co.); GENERIC DESCRIPTOR (the orchard "
            "partners, the mead company, the meadery, the LP account, the "
            "farms LP); MISMATCHED-vs-TYPO (Pacific Coast Honey null vs "
            "Casdaia / Pacfic / Partnrs / Fod pick).  Rule 3 splits into (a) "
            "CUSTOMER + SITE-QUALIFIER (Drakestone Main Warehouse → Drakestone "
            "Mead) and (b) SITE-ONLY ambiguous (Highway Block 2 → null).  "
            "SKU Rule 2 enumerates ambiguous families (raw honey jar, queen "
            "excluder, varroa treatment, honey pail/drum, bottom board) and "
            "specific resolutions (Italian/Russian/Carniolan queens, clover "
            "honey drum); Rule 3 adds QUANTITY MISMATCH IS NOT AMBIGUITY so "
            "'100 lb of winter fondant' still resolves to the 50-lb pack SKU.\n"
            "\n"
            "Iteration journey (subagent verified): 4 internal cycles, of "
            "which an attempt to add trailing verb-clause and prep-clause "
            "stripping was reverted — those regexes cut at the first verb/"
            "prep which is often BEFORE the entity ('What queens did we ship "
            "to Goldenridge Fruit Co.' → 'what queens')."
        ),
    },
]
