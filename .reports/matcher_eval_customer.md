# Eval Report: matcher_eval_customer

**Generated:** 2026-05-19 21:23 UTC  
**Git SHA:** `f52debb5`  
**Corpus SHA:** `27d19a606e66`  
**Eval mode:** `full`  
**Result:** FAILURES PRESENT  

Overall precision: **95.2%** (200/210)

## Per-Category Results

| Category | Precision | Pass/Total | Threshold | Status |
|---|---|---|---|---|
| `abbreviation` | 91.7% | 11/12 | 98% | FAIL |
| `alias` | 92.3% | 12/13 | 98% | FAIL |
| `ambiguous_reference` | 100.0% | 11/11 | 95% | PASS |
| `apostrophe_variant` | 88.9% | 8/9 | 98% | FAIL |
| `canonical` | 100.0% | 16/16 | 100% | PASS |
| `casual_colloquial` | 92.3% | 12/13 | 95% | FAIL |
| `compound_reference` | 90.0% | 9/10 | 85% | PASS |
| `cross_customer_collision` | 90.9% | 10/11 | 90% | PASS |
| `generic_words` | 100.0% | 9/9 | 100% | PASS |
| `long_form_sentence` | 100.0% | 24/24 | 95% | PASS |
| `negative_case` | 100.0% | 16/16 | 100% | PASS |
| `partial_match_noise` | 100.0% | 8/8 | 95% | PASS |
| `possessive` | 100.0% | 9/9 | 98% | PASS |
| `pronoun_resolution` | 100.0% | 11/11 | 85% | PASS |
| `site_reference` | 86.7% | 13/15 | 95% | FAIL |
| `site_shortform` | 75.0% | 6/8 | 95% | FAIL |
| `typo_resolvable` | 100.0% | 15/15 | 90% | PASS |

## Failure Details

### `abbreviation` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Pacific Honey` | 'Pacific Honey Wholesale' | None |  |

### `alias` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `7 Acres Farms` | 'Seven Acres Farms' | None |  |

### `apostrophe_variant` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Mountain Comb Company` | 'Mountain Comb Co.' | None |  |

### `casual_colloquial` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `the Cascadia honey crew` | 'Cascadia Honey Co.' | None |  |

### `compound_reference` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Martinez north block` | 'Martinez & Sons Orchards' | 'Juniper Hill Orchard Partners' |  |

### `cross_customer_collision` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Seven Acres Farms` | None | 'Seven Acres Farms' |  |

### `site_reference` failures (2)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Cobblestone Main Warehouse` | 'Cobblestone Hives' | None |  |
| `Drakestone Main Warehouse` | 'Drakestone Mead' | 'Cobblestone Hives' |  |

### `site_shortform` failures (2)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Martinez hilltop` | 'Martinez & Sons Orchards' | None |  |
| `Haley orchard road site` | 'Haley Orchard Co.' | None |  |

