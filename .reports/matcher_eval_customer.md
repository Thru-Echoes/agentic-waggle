# Eval Report: matcher_eval_customer

**Generated:** 2026-05-13 18:25 UTC  
**Git SHA:** `ca764383`  
**Corpus SHA:** `df2d7b1dc0fc`  
**Eval mode:** `sample`  
**Result:** FAILURES PRESENT  

Overall precision: **75.0%** (36/48)

## Per-Category Results

| Category | Precision | Pass/Total | Threshold | Status |
|---|---|---|---|---|
| `abbreviation` | 100.0% | 3/3 | 98% | PASS |
| `alias` | 66.7% | 2/3 | 98% | FAIL |
| `ambiguous_reference` | 66.7% | 2/3 | 95% | FAIL |
| `apostrophe_variant` | 100.0% | 2/2 | 98% | PASS |
| `canonical` | 50.0% | 2/4 | 100% | FAIL |
| `casual_colloquial` | 66.7% | 2/3 | 95% | FAIL |
| `compound_reference` | 0.0% | 0/2 | 85% | FAIL |
| `cross_customer_collision` | 100.0% | 3/3 | 90% | PASS |
| `generic_words` | 100.0% | 2/2 | 100% | PASS |
| `long_form_sentence` | 100.0% | 5/5 | 95% | PASS |
| `negative_case` | 100.0% | 3/3 | 100% | PASS |
| `partial_match_noise` | 100.0% | 2/2 | 95% | PASS |
| `possessive` | 50.0% | 1/2 | 98% | FAIL |
| `pronoun_resolution` | 66.7% | 2/3 | 85% | FAIL |
| `site_reference` | 66.7% | 2/3 | 95% | FAIL |
| `site_shortform` | 50.0% | 1/2 | 95% | FAIL |
| `typo_resolvable` | 66.7% | 2/3 | 90% | FAIL |

## Failure Details

### `alias` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Five Rivers Bee Co.` | 'Five Rivers Apiaries' | None |  |

### `ambiguous_reference` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `the family orchards` | None | "O'Reilly Family Orchards" |  |

### `canonical` failures (2)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Blue Ridge Farms` | 'Blue Ridge Farms' | None |  |
| `Mesa Orchard Co.` | 'Mesa Orchard Co.' | None |  |

### `casual_colloquial` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Drakestone guys` | 'Drakestone Mead' | None |  |

### `compound_reference` failures (2)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Wenatchee Family Orchards river block` | 'Wenatchee Family Orchards' | 'Wenatchee Orchard Partners' |  |
| `Martinez north block` | 'Martinez & Sons Orchards' | 'Juniper Hill Orchard Partners' |  |

### `possessive` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Cobblestone's` | 'Cobblestone Hives' | None |  |

### `pronoun_resolution` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Log a note that they confirmed the spring pollination contract.` | 'St. Agnes Pollination' | None |  |

### `site_reference` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Hilltop 1` | None | 'Mountain Valley Farms' |  |

### `site_shortform` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Riverbend hilltop` | None | 'Riverbend Family Orchards' |  |

### `typo_resolvable` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Cobblstone Hives` | 'Cobblestone Hives' | None |  |

