# Matcher Corpus Review — Independent Thoroughness Assessment

**Reviewed by:** Independent subagent  
**Date:** 2026-05-13  
**Corpus file:** `tests/fixtures/matcher_corpus.yaml`  
**Total cases reviewed:** 264 (24 categories)  
**DB verified against:** Live seed at `postgresql://agentic_mcp_crm:...@localhost:5433/agentic_mcp_crm`

---

## Summary Verdict

**NEEDS REVISIONS** — The corpus is structurally sound and all category counts meet or exceed minimums. No false-positive customer name failures were found (all 36 expected customer names exist in the DB; all 23 expected SKU codes exist in the DB/services tables). However, there are five substantive issues that must be fixed before the corpus can serve as a reliable spec:

1. Two exact duplicate inputs span different categories (should be deduplicated or differentiated).
2. Four seeded customers are never exercised as a direct resolution target, including two that are collision partners in categories that reference them only as ambiguous.
3. The `sku_colloquial` case `"varroa treatment" → TRT-OXALIC-R` is incorrectly narrow — both oxalic and formic treat varroa, making this a `sku_ambiguous` case.
4. `"bee package 3lb" → PKG-BEES-3LB-ITA` silently assumes Italian breed without that being stated in the input.
5. Real dictated-speech artifacts (mid-sentence self-corrections, phonetic transcription errors, quantity+customer compounds in a single utterance) are entirely absent from the corpus.

---

## (a) Missing Variation Categories

### Gap 1 — Dictated-speech self-corrections and mid-sentence interruptions

**Zero cases in the corpus model this pattern.** Real field-rep transcripts contain self-corrections ("call Smith — no, I mean Schmidt"), trailing-off inputs, and mid-sentence name substitutions. These stress the disambiguation layer in ways that clean typos do not, because the pre-correction text is a plausible name on its own.

Suggested additions (new category `dictation_self_correction` or folded into `casual_colloquial`/`typo_resolvable`):

```yaml
- input: "call — what's his name — the Martinez guy"
  expected_customer_name: "Martinez & Sons Orchards"
  category: casual_colloquial

- input: "it's for Blue Ridge, no — Blue Ridge Family Orchards, the almond one"
  expected_customer_name: "Blue Ridge Family Orchards"
  category: casual_colloquial

- input: "O'Reilly — sorry, I mean Five Rivers Apiaries"
  expected_customer_name: "Five Rivers Apiaries"
  category: casual_colloquial

- input: "Mountain Valley Farms — or is it Mountain Valley Orchards, the one near Fresno"
  expected_customer_name: null
  expected_alternatives_min: 2
  category: ambiguous_reference
```

### Gap 2 — Phonetic transcription artifacts (dictated speech homophones and spacing errors)

No case tests what happens when speech-to-text produces a plausible but wrong rendering. Examples: "O'Riley" (Irish name phonetically plausible), "Hay Lee" for "Haley", "Cobb Le Stone" for "Cobblestone", "star chaser" for "Stormchaser".

Suggested additions (folded into `typo_resolvable`):

```yaml
- input: "O'Riley Family Orchards"
  expected_customer_name: "O'Reilly Family Orchards"
  category: typo_resolvable

- input: "Hay Lee Farms LP"
  expected_customer_name: "Haley Farms LP"
  category: typo_resolvable

- input: "star chaser meadery"
  expected_customer_name: "Stormchaser Meadery"
  category: typo_resolvable

- input: "drake stone mead"
  expected_customer_name: "Drakestone Mead"
  category: typo_resolvable
```

### Gap 3 — Combined customer + SKU references in a single utterance

The `compound_reference` category tests customer name + site location. There is no category testing customer name + SKU in a single input ("20 Italian queens for Martinez" is the archetypal field-rep utterance). The corpus is meant to drive a matcher that may receive either, but combined inputs are not exercised at all.

Suggested additions (new category `sku_with_customer` or folded into `long_form_sentence`):

```yaml
- input: "20 Italian mated queens for Martinez"
  expected_customer_name: "Martinez & Sons Orchards"
  expected_sku_code: "QUEEN-ITA-MATED"
  category: long_form_sentence

- input: "3 lb Italian bee package for O'Reilly highway block"
  expected_customer_name: "O'Reilly Family Orchards"
  expected_sku_code: "PKG-BEES-3LB-ITA"
  category: long_form_sentence

- input: "almond pollination premium for Five Rivers"
  expected_customer_name: "Five Rivers Apiaries"
  expected_sku_code: "POL_ALMOND_PREMIUM"
  category: long_form_sentence
```

### Gap 4 — Near-miss negative cases (inputs that look like real customers but are not)

The current 13 negative cases use clearly-invented names (`Sunflower Apiary`, `Desert Bloom Farms`). None of them exploit the actual alias/abbreviation structure of the seed. A matcher that over-extends on partial token matches would pass all current negatives but fail on:

- `"Pacific Coast Honey"` — looks like `"Pacific Honey Wholesale"` (alias: `"Pacific Honey"`); corpus marks this as a true negative but does NOT warn that `Pacific Honey` is a known alias, so the matcher may legitimately fire on it. This case needs a note or should be recategorized as `ambiguous_reference`.
- Names constructed from real seed tokens: `"Mountain Comb Partners"`, `"Blue Ridge Bee Co."`, `"Seven Acres Orchards"`.

Suggested additions:

```yaml
- input: "Mountain Comb Partners"
  expected_customer_name: null
  category: negative_case

- input: "Blue Ridge Bee Co."
  expected_customer_name: null
  category: negative_case

- input: "Seven Acres Orchards"
  expected_customer_name: null
  category: negative_case

- input: "Pacific Coast Honey"
  expected_customer_name: null
  expected_alternatives_min: 1
  category: ambiguous_reference
  # Note: "Pacific Honey" is an alias for Pacific Honey Wholesale;
  # matcher may surface it — test should tolerate that ambiguity rather than assert null.
```

### Gap 5 — Years and numbered site labels embedded in informal references

Real rep speech: "the 2024 yard", "site number three", "the new Wenatchee block". The corpus has numbered block names in `site_reference` (e.g., `"Martinez North Block 3"`) but no informal "the new", "the old", "site 2" style references that lack a clear customer anchor:

```yaml
- input: "the old Haley site"
  expected_customer_name: null
  expected_alternatives_min: 2
  category: ambiguous_reference

- input: "Riverbend site 2"
  expected_customer_name: null
  expected_alternatives_min: 2
  category: site_reference

- input: "O'Reilly's new block"
  expected_customer_name: "O'Reilly Family Orchards"
  category: casual_colloquial
```

---

## (b) Filler / Tautological Cases

### Duplicate exact input: `"Blue Ridge"` in both `alias` and `cross_customer_collision`

Lines 158–161 and lines 479–481. The input, expected output, and semantics are identical. One of these must be removed or one must be differentiated (e.g., the alias instance should have a specific expected_customer_name with context, since `alias` implies a known alias form, not a collision).

**Weak case (alias, line 158):**
```yaml
- input: "Blue Ridge"
  expected_customer_name: null
  expected_alternatives_min: 2
  category: alias
```
`"Blue Ridge"` is _not_ an alias in the DB — it is a shared prefix creating a collision. It belongs only in `cross_customer_collision`. The alias category should use `"Blue Ridge Family"` (the actual alias for `Blue Ridge Family Orchards`) instead.

### Duplicate exact input: `"O'Reilly west block"` in both `site_shortform` and `compound_reference`

Lines 281–283 and 661–663. The input is character-for-character identical; only the category label differs. The conceptual distinction (shortform vs compound reference) is too fine to justify testing the same string twice. One instance should be differentiated — e.g., use `"O'Reilly's west block"` (possessive form) in one.

### Long-form sentences: several are structurally identical to canonical

```yaml
- input: "Can you pull up the account for O'Reilly Family Orchards?"
  expected_customer_name: "O'Reilly Family Orchards"
  category: long_form_sentence

- input: "The Foothill Orchards site needs a full hive inspection."
  expected_customer_name: "Foothill Orchards"
  category: long_form_sentence
```

These 16 cases all follow the same template: `[verb phrase] [exact canonical name] [trailing clause]`. The canonical name is not altered at all, so the only thing being tested is that the matcher can extract a name from a sentence. This is useful, but 21 such cases (versus the 15-case minimum) means 6 extra near-identical-shape cases. The category would be stronger with 2–3 cases that embed _abbreviated_ or _misquoted_ names rather than exact canonicals, e.g.:

```yaml
- input: "Schedule a delivery for the Martinez orchard next Tuesday."
  expected_customer_name: "Martinez & Sons Orchards"
  category: long_form_sentence
```

This forces the matcher to both extract from sentence AND resolve an abbreviation, which is the actual hard case in production.

### Partial match noise: all 8 cases use exact canonical name

Every `partial_match_noise` case wraps the exact canonical name in noise text (`"re: Martinez & Sons Orchards — urgent"`). None uses an abbreviated, possessive, or alias form within the noise. The category exists to test extraction from noisy context, but once extraction is assumed, the resolution is trivially canonical. At least 2 of the 8 should use a non-canonical form inside the noise:

```yaml
- input: "re: the Martinez folks — urgent follow-up needed"
  expected_customer_name: "Martinez & Sons Orchards"
  category: partial_match_noise
```

---

## (c) False-Positive Expected Names

**No false-positive customer name failures found.** All 36 distinct expected customer names in the corpus exist in the live DB. All 23 expected SKU codes exist in the DB (products + services).

However, three cases reference expected names that are **resolution-correct but structurally misleading**:

### Case: `"Wenatchee Orchard Partners"` — claimed as expected in `long_form_sentence`

Line 409: `"Can you log a note on the Wenatchee Family Orchards account?"` → `expected_customer_name: "Wenatchee Family Orchards"`. This is correct.

But line 346–347 (typo_resolvable): `"Wenatchee Orcard Partners"` → `"Wenatchee Orchard Partners"`. Both `Wenatchee Family Orchards` and `Wenatchee Orchard Partners` exist in DB. This is correct.

The concern is that `Wenatchee Orchard Partners` is **never exercised as a direct non-typo resolution target**. Its only appearance is as the target of a typo correction. A field rep could legitimately reference it in a long_form or abbreviation case.

### Four customers never appear as a direct expected match in any case

The following seed customers are never the `expected_customer_name` in any non-ambiguous case:

- `"Blue Ridge Farms"` — the collision sibling of `Blue Ridge Family Orchards`. Never resolved directly.
- `"Mesa Orchard Co."` — entirely absent from corpus. No case targets it.
- `"Mountain Comb Wholesale"` — only appears implicitly as the collision partner in `"Mountain Comb"` cross-collision. Never a direct resolution target.
- `"Mountain Valley Orchards"` — only appears as a collision partner. The `long_form_sentence` case uses `Mountain Valley Farms`, not `Mountain Valley Orchards`.

These four need at least one canonical or abbreviation case each, otherwise the matcher can fail to resolve them entirely without any test catching it.

**Suggested additions:**

```yaml
- input: "Blue Ridge Farms"
  expected_customer_name: "Blue Ridge Farms"
  category: canonical

- input: "Mesa Orchard"
  expected_customer_name: "Mesa Orchard Co."
  category: abbreviation

- input: "Mountain Comb Wholesale"
  expected_customer_name: "Mountain Comb Wholesale"
  category: canonical

- input: "Mountain Valley Orchards"
  expected_customer_name: "Mountain Valley Orchards"
  category: canonical
```

---

## (d) Category Threshold Calibration

All 24 categories meet or exceed their minimums (verified by count):

| Category | Count | Min | Status |
|---|---|---|---|
| canonical | 12 | 10 | OK |
| abbreviation | 12 | 10 | OK |
| possessive | 9 | 8 | OK |
| alias | 13 | 10 | OK |
| apostrophe_variant | 9 | 6 | OK |
| site_reference | 15 | 12 | OK |
| site_shortform | 8 | 8 | OK (at minimum) |
| typo_resolvable | 15 | 10 | OK |
| long_form_sentence | 21 | 15 | OK (excess is filler per §b) |
| pronoun_resolution | 11 | 10 | OK |
| cross_customer_collision | 11 | 10 | OK |
| ambiguous_reference | 11 | 10 | OK |
| negative_case | 13 | 10 | OK |
| generic_words | 9 | 8 | OK |
| compound_reference | 10 | 8 | OK |
| casual_colloquial | 10 | 8 | OK |
| partial_match_noise | 8 | 6 | OK |
| sku_canonical_code | 10 | 10 | OK (at minimum) |
| sku_product_name | 13 | 10 | OK |
| sku_qty_plus_product | 12 | 10 | OK |
| sku_service | 6 | 6 | OK (at minimum) |
| sku_colloquial | 12 | 8 | OK |
| sku_ambiguous | 6 | 6 | OK (at minimum) |
| sku_negative | 8 | 6 | OK |

### Adjustments recommended

**`sku_colloquial` — one case miscategorized:**  
`"varroa treatment" → TRT-OXALIC-R` should be `sku_ambiguous` with `expected_alternatives_min: 2`. Both `TRT-OXALIC-R` (oxalic acid) and `TRT-FORMIC-R` (formic acid) treat varroa mites. Assigning this to a single SKU in `sku_colloquial` is incorrect — a well-calibrated matcher will (correctly) surface both. Fixing this drops `sku_colloquial` to 11 and raises `sku_ambiguous` to 7, keeping both above minimum.

**`sku_colloquial` — one case is too narrow:**  
`"bee package 3lb" → PKG-BEES-3LB-ITA` implicitly resolves to Italian because the DB only has Italian packages. This is fine if the test intent is "3lb package → Italian," but the input doesn't specify the lineage. Either add `"3 lb Italian bee package"` as the `sku_product_name` case and make the colloquial form `sku_ambiguous` (since a "3lb package" could plausibly be any lineage), or add a note in the case acknowledging the single-lineage assumption.

**`sku_product_name` — vastly under-tests the honey category:**  
The DB has 20 honey SKUs; the corpus tests exactly 3 honey varietal name resolutions (clover/wildflower/orange-blossom). The remaining 17 honey SKUs (sage, buckwheat, tupelo, alfalfa, sourwood, macadamia-blossom, manuka-style, plus all Grade B and raw-jar variants) are never tested at all. This is acceptable if the design intent is "we don't need to test every varietal," but it means a regression in sage-honey or buckwheat-honey resolution goes undetected. Recommend adding at least 3 more honey tests covering different varietal families:

```yaml
- input: "sage honey pail"
  expected_sku_kind: "product"
  expected_sku_code: "HONEY-SAG-A-PAIL"
  category: sku_product_name

- input: "buckwheat honey 60 lb"
  expected_sku_kind: "product"
  expected_sku_code: "HONEY-BKW-A-PAIL"
  category: sku_product_name

- input: "wildflower honey drum Grade A"
  expected_sku_kind: "product"
  expected_sku_code: "HONEY-WLD-A-DRUM"
  category: sku_product_name
```

**`site_shortform` — at minimum with one structurally weak case:**  
`"Haley orchard road site" → "Haley Orchard Co."` works because `Haley Orchard Co.` has an `Orchard Rd 1` site. But this is the only case that leverages actual site name data; the rest use generic block types (`hilltop`, `river block`, `warehouse`). If the seed site names changed, this case would silently become wrong. It's not filler, but it is fragile. A comment noting the site dependency would help.

**`sku_service` — at minimum, no dictated/oral form tested:**  
All 6 service cases are clean noun phrases. There is no case where a field rep says something like "we need pollination for almonds next spring" (which maps to `POL_ALMOND_PREMIUM` but requires the matcher to extract intent from context). Adding one looser form would improve coverage:

```yaml
- input: "blueberry pollination per acre"
  expected_sku_kind: "service"
  expected_sku_code: "POL_BERRY"
  category: sku_service
```

---

## (e) Cross-Customer Collision Coverage

The three known collision families are verified against the DB and corpus:

### "Blue Ridge" — PRESENT, but split incorrectly

- DB: `Blue Ridge Family Orchards` (alias: `["Blue Ridge Family"]`), `Blue Ridge Farms` (alias: `["Blue Ridge"]`)
- Corpus: `"Blue Ridge"` appears in both `alias` (line 158) and `cross_customer_collision` (line 479), both with `expected_alternatives_min: 2`.
- **Problem:** `"Blue Ridge"` is in fact an alias **specifically for `Blue Ridge Farms`** in the DB (`aliases = ["Blue Ridge"]`). It is _not_ ambiguous by alias — it should resolve deterministically to `Blue Ridge Farms`. The ambiguity arises from the *prefix match* `"Blue Ridge"` against both names, not from the alias system. The corpus treating this as a two-alternative collision (at the alias level) is **incorrect**. The alias case should use `"Blue Ridge Family"` → `Blue Ridge Family Orchards` (the real alias). The `cross_customer_collision` case for bare `"Blue Ridge"` remains valid as a prefix-collision test.

### "Mountain Valley" — PRESENT and correct

- DB: `Mountain Valley Farms` (alias: `["Mountain Valley"]`), `Mountain Valley Orchards` (alias: `["Mountain Valley"]`)
- Both carry the alias `"Mountain Valley"` — this is a genuine alias collision.
- Corpus: `"Mountain Valley"` → `expected_alternatives_min: 2` in `cross_customer_collision`. CORRECT.

### "Haley" (3 customers) — PRESENT and correct

- DB: `Haley Family Orchards`, `Haley Farms LP`, `Haley Orchard Co.`
- Corpus: `"Haley"` → `expected_alternatives_min: 3` in `ambiguous_reference`. CORRECT.
- Corpus: `"Haley's"` → `expected_alternatives_min: 2` in `possessive` (note: this is `alts_min=2`, not 3; the intent may be that possessive narrows the candidates or that only 2 are triggered by this form — this is acceptable but ambiguous in spec).
- Corpus: `"Haley Farms"` → `"Haley Farms LP"` (with context `active_account_name`) in `cross_customer_collision`. CORRECT — context resolves ambiguity.

### Additional collisions verified in corpus

| Collision | DB entries | Corpus category | alts_min | Status |
|---|---|---|---|---|
| `"Wenatchee"` | Wenatchee Family Orchards + Wenatchee Orchard Partners | cross_customer_collision | 2 | CORRECT |
| `"Mountain Comb"` | Mountain Comb Co. + Mountain Comb Wholesale | cross_customer_collision | 2 | CORRECT |
| `"Seven Acres"` | Seven Acres Farms + Seven Acres Farms LP | cross_customer_collision | 2 | CORRECT |
| `"Seven Acres Farms"` | Seven Acres Farms + Seven Acres Farms LP | cross_customer_collision | 2 | CORRECT |
| `"Riverbend"` | Riverbend Family Orchards + Riverbend Farms LP | cross_customer_collision | 2 | CORRECT |
| `"Old Mission"` | Old Mission Farm + Old Mission Growers | cross_customer_collision | 2 | CORRECT |
| `"Juniper"` | Juniper Bee Co. + Juniper Hill Orchard Partners | cross_customer_collision | 2 | CORRECT |

**One collision absent from the corpus:** `"Wenatchee Orchard"` (alias for `Wenatchee Orchard Partners`) vs the broader "Wenatchee" collision. The alias case handles `"Wenatchee Orchard"` → `Wenatchee Orchard Partners`, which is correct and not a collision. This is fine.

**One collision absent from the corpus:** `"Mountain Valley"` is a collision alias but `"Mountain Valley Orchards"` is never exercised as a direct match target (see §c).

---

## Summary of Required Changes

Priority order:

1. **Remove the `"Blue Ridge"` alias duplicate** (keep in `cross_customer_collision`; replace alias instance with `"Blue Ridge Family"` → `Blue Ridge Family Orchards`).
2. **Remove the `"O'Reilly west block"` site_shortform/compound_reference duplicate** (differentiate with possessive form in one).
3. **Move `"varroa treatment"` from `sku_colloquial` to `sku_ambiguous`** (both oxalic and formic treat varroa; expected_alternatives_min: 2, drop single SKU expectation).
4. **Add 4 canonical/abbreviation cases** for `Blue Ridge Farms`, `Mesa Orchard Co.`, `Mountain Comb Wholesale`, `Mountain Valley Orchards`.
5. **Add 3–5 dictated-speech self-correction cases** (see §a Gap 1).
6. **Add 3 honey varietal product name tests** covering sage, buckwheat, wildflower Grade A drum (see §d).
7. **Add near-miss negatives** using real-seed token combinations (see §a Gap 4).
8. **Add 2–3 combined customer+SKU cases** to `long_form_sentence` (see §a Gap 3).

Items 1–4 are correctness fixes. Items 5–8 are coverage improvements.

---

*Report written: 2026-05-13. Full DB query data available in session context.*
