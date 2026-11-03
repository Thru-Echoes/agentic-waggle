# Eval Report: matcher_eval_customer

**Generated:** 2026-11-03 01:09 UTC  
**Git SHA:** `9f1d28bd`  
**Corpus SHA:** `27d19a606e66`  
**Eval mode:** `full`  
**Result:** FAILURES PRESENT  

Overall precision: **95.2%** (200/210)

## Per-Category Results

| Category | Precision | Pass/Total | Threshold | Status |
|---|---|---|---|---|
| `abbreviation` | 100.0% | 12/12 | 98% | PASS |
| `alias` | 92.3% | 12/13 | 98% | FAIL |
| `ambiguous_reference` | 90.9% | 10/11 | 95% | FAIL |
| `apostrophe_variant` | 100.0% | 9/9 | 98% | PASS |
| `canonical` | 100.0% | 16/16 | 100% | PASS |
| `casual_colloquial` | 100.0% | 13/13 | 95% | PASS |
| `compound_reference` | 90.0% | 9/10 | 85% | PASS |
| `cross_customer_collision` | 100.0% | 11/11 | 90% | PASS |
| `generic_words` | 100.0% | 9/9 | 100% | PASS |
| `long_form_sentence` | 95.8% | 23/24 | 95% | PASS |
| `negative_case` | 100.0% | 16/16 | 100% | PASS |
| `partial_match_noise` | 100.0% | 8/8 | 95% | PASS |
| `possessive` | 100.0% | 9/9 | 98% | PASS |
| `pronoun_resolution` | 100.0% | 11/11 | 85% | PASS |
| `site_reference` | 86.7% | 13/15 | 95% | FAIL |
| `site_shortform` | 50.0% | 4/8 | 95% | FAIL |
| `typo_resolvable` | 100.0% | 15/15 | 90% | PASS |

## Failure Details

### `alias` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `7 Acres Farms` | 'Seven Acres Farms' | None |  |

### `ambiguous_reference` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Cascade` | None | 'Cascade Orchard Partners' |  |

### `compound_reference` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Martinez north block` | 'Martinez & Sons Orchards' | 'Juniper Hill Orchard Partners' |  |

### `long_form_sentence` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Seven Acres Farms LP called about their fall honey harvest schedule.` | 'Seven Acres Farms LP' | None |  |

### `site_reference` failures (2)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Cobblestone Main Warehouse` | 'Cobblestone Hives' | None |  |
| `Drakestone Main Warehouse` | 'Drakestone Mead' | None |  |

### `site_shortform` failures (4)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Martinez hilltop` | 'Martinez & Sons Orchards' | None |  |
| `Foothill river block` | 'Foothill Orchards' | None |  |
| `Haley orchard road site` | 'Haley Orchard Co.' | None |  |
| `Old Mission orchard road` | None | 'Old Mission Farm' |  |

