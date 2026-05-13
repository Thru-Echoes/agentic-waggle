# Eval Report: matcher_eval_customer

**Generated:** 2026-05-13 15:48 UTC  
**Git SHA:** `db2a4b0d`  
**Corpus SHA:** `df2d7b1dc0fc`  
**Eval mode:** `sample`  
**Result:** FAILURES PRESENT  

Overall precision: **77.1%** (37/48)

## Per-Category Results

| Category | Precision | Pass/Total | Threshold | Status |
|---|---|---|---|---|
| `abbreviation` | 100.0% | 3/3 | 98% | PASS |
| `alias` | 100.0% | 3/3 | 98% | PASS |
| `ambiguous_reference` | 33.3% | 1/3 | 95% | FAIL |
| `apostrophe_variant` | 100.0% | 2/2 | 98% | PASS |
| `canonical` | 100.0% | 4/4 | 100% | PASS |
| `casual_colloquial` | 66.7% | 2/3 | 95% | FAIL |
| `compound_reference` | 50.0% | 1/2 | 85% | FAIL |
| `cross_customer_collision` | 66.7% | 2/3 | 90% | FAIL |
| `generic_words` | 100.0% | 2/2 | 100% | PASS |
| `long_form_sentence` | 100.0% | 5/5 | 95% | PASS |
| `negative_case` | 100.0% | 3/3 | 100% | PASS |
| `partial_match_noise` | 100.0% | 2/2 | 95% | PASS |
| `possessive` | 50.0% | 1/2 | 98% | FAIL |
| `pronoun_resolution` | 33.3% | 1/3 | 85% | FAIL |
| `site_reference` | 66.7% | 2/3 | 95% | FAIL |
| `site_shortform` | 50.0% | 1/2 | 95% | FAIL |
| `typo_resolvable` | 66.7% | 2/3 | 90% | FAIL |

## Failure Details

### `ambiguous_reference` failures (2)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Haley` | None | 'Haley Orchard Co.' |  |
| `the family orchards` | None | "O'Reilly Family Orchards" |  |

### `casual_colloquial` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Drakestone guys` | 'Drakestone Mead' | None |  |

### `compound_reference` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Martinez north block` | 'Martinez & Sons Orchards' | None |  |

### `cross_customer_collision` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Riverbend` | None | 'Riverbend Family Orchards' |  |

### `possessive` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Cobblestone's` | 'Cobblestone Hives' | None |  |

### `pronoun_resolution` failures (2)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Can you pull up their inspection records?` | 'Martinez & Sons Orchards' | 'Mesa Orchard Co.' |  |
| `They confirmed the new contract.` | 'Sierra Crest Orchard Partners' | None |  |

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

