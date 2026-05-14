# Eval Report: matcher_eval_customer

**Generated:** 2026-05-14 12:23 UTC  
**Git SHA:** `e6068b95`  
**Corpus SHA:** `df2d7b1dc0fc`  
**Eval mode:** `full`  
**Result:** FAILURES PRESENT  

Overall precision: **94.2%** (195/207)

## Per-Category Results

| Category | Precision | Pass/Total | Threshold | Status |
|---|---|---|---|---|
| `abbreviation` | 91.7% | 11/12 | 98% | FAIL |
| `alias` | 84.6% | 11/13 | 98% | FAIL |
| `ambiguous_reference` | 90.9% | 10/11 | 95% | FAIL |
| `apostrophe_variant` | 100.0% | 9/9 | 98% | PASS |
| `canonical` | 100.0% | 16/16 | 100% | PASS |
| `casual_colloquial` | 92.3% | 12/13 | 95% | FAIL |
| `compound_reference` | 80.0% | 8/10 | 85% | FAIL |
| `cross_customer_collision` | 90.9% | 10/11 | 90% | PASS |
| `generic_words` | 100.0% | 9/9 | 100% | PASS |
| `long_form_sentence` | 100.0% | 24/24 | 95% | PASS |
| `negative_case` | 100.0% | 13/13 | 100% | PASS |
| `partial_match_noise` | 100.0% | 8/8 | 95% | PASS |
| `possessive` | 100.0% | 9/9 | 98% | PASS |
| `pronoun_resolution` | 90.9% | 10/11 | 85% | PASS |
| `site_reference` | 86.7% | 13/15 | 95% | FAIL |
| `site_shortform` | 87.5% | 7/8 | 95% | FAIL |
| `typo_resolvable` | 100.0% | 15/15 | 90% | PASS |

## Failure Details

### `abbreviation` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Foothill` | 'Foothill Orchards' | 'Blue Ridge Farms' |  |

### `alias` failures (2)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Haley Farms Limited Partnership` | 'Haley Farms LP' | None |  |
| `Cascadia Honey Company` | 'Cascadia Honey Co.' | None |  |

### `ambiguous_reference` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Cascade` | None | 'Cascade Orchard Partners' |  |

### `casual_colloquial` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Wenatchee family guys` | 'Wenatchee Family Orchards' | None |  |

### `compound_reference` failures (2)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Martinez north block` | 'Martinez & Sons Orchards' | None |  |
| `St. Agnes highway block` | 'St. Agnes Pollination' | "O'Reilly Family Orchards" |  |

### `cross_customer_collision` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Seven Acres Farms` | None | 'Seven Acres Farms' |  |

### `pronoun_resolution` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `How many hives do they have?` | 'Five Rivers Apiaries' | 'Beelore & Co.' |  |

### `site_reference` failures (2)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Cobblestone Main Warehouse` | 'Cobblestone Hives' | 'Heartland Apiary Trading Co.' |  |
| `Drakestone Main Warehouse` | 'Drakestone Mead' | 'Cobblestone Hives' |  |

### `site_shortform` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Haley orchard road site` | 'Haley Orchard Co.' | None |  |

