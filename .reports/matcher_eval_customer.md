# Eval Report: matcher_eval_customer

**Generated:** 2026-05-14 15:16 UTC  
**Git SHA:** `87aa833a`  
**Corpus SHA:** `31866c57aa52`  
**Eval mode:** `full`  
**Result:** FAILURES PRESENT  

Overall precision: **95.2%** (200/210)

## Per-Category Results

| Category | Precision | Pass/Total | Threshold | Status |
|---|---|---|---|---|
| `abbreviation` | 100.0% | 12/12 | 98% | PASS |
| `alias` | 100.0% | 13/13 | 98% | PASS |
| `ambiguous_reference` | 90.9% | 10/11 | 95% | FAIL |
| `apostrophe_variant` | 100.0% | 9/9 | 98% | PASS |
| `canonical` | 100.0% | 16/16 | 100% | PASS |
| `casual_colloquial` | 100.0% | 13/13 | 95% | PASS |
| `compound_reference` | 90.0% | 9/10 | 85% | PASS |
| `cross_customer_collision` | 72.7% | 8/11 | 90% | FAIL |
| `generic_words` | 100.0% | 9/9 | 100% | PASS |
| `long_form_sentence` | 100.0% | 24/24 | 95% | PASS |
| `negative_case` | 100.0% | 16/16 | 100% | PASS |
| `partial_match_noise` | 100.0% | 8/8 | 95% | PASS |
| `possessive` | 100.0% | 9/9 | 98% | PASS |
| `pronoun_resolution` | 100.0% | 11/11 | 85% | PASS |
| `site_reference` | 86.7% | 13/15 | 95% | FAIL |
| `site_shortform` | 62.5% | 5/8 | 95% | FAIL |
| `typo_resolvable` | 100.0% | 15/15 | 90% | PASS |

## Failure Details

### `ambiguous_reference` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Cascade` | None | 'Cascade Orchard Partners' |  |

### `compound_reference` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Martinez north block` | 'Martinez & Sons Orchards' | None |  |

### `cross_customer_collision` failures (3)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Riverbend` | None | 'Riverbend Family Orchards' |  |
| `Wenatchee` | None | 'Wenatchee Orchard Partners' |  |
| `Seven Acres Farms` | None | 'Seven Acres Farms' |  |

### `site_reference` failures (2)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Cobblestone Main Warehouse` | 'Cobblestone Hives' | None |  |
| `Drakestone Main Warehouse` | 'Drakestone Mead' | 'Mountain Comb Wholesale' |  |

### `site_shortform` failures (3)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Martinez hilltop` | 'Martinez & Sons Orchards' | None |  |
| `Foothill river block` | 'Foothill Orchards' | None |  |
| `Haley orchard road site` | 'Haley Orchard Co.' | None |  |

