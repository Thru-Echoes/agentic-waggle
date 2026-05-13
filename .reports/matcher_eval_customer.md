# Eval Report: matcher_eval_customer

**Generated:** 2026-05-13 17:56 UTC  
**Git SHA:** `cdd4c7ab`  
**Corpus SHA:** `df2d7b1dc0fc`  
**Eval mode:** `sample`  
**Result:** FAILURES PRESENT  

Overall precision: **52.1%** (25/48)

## Per-Category Results

| Category | Precision | Pass/Total | Threshold | Status |
|---|---|---|---|---|
| `abbreviation` | 33.3% | 1/3 | 98% | FAIL |
| `alias` | 66.7% | 2/3 | 98% | FAIL |
| `ambiguous_reference` | 100.0% | 3/3 | 95% | PASS |
| `apostrophe_variant` | 50.0% | 1/2 | 98% | FAIL |
| `canonical` | 50.0% | 2/4 | 100% | FAIL |
| `casual_colloquial` | 33.3% | 1/3 | 95% | FAIL |
| `compound_reference` | 0.0% | 0/2 | 85% | FAIL |
| `cross_customer_collision` | 66.7% | 2/3 | 90% | FAIL |
| `generic_words` | 100.0% | 2/2 | 100% | PASS |
| `long_form_sentence` | 20.0% | 1/5 | 95% | FAIL |
| `negative_case` | 100.0% | 3/3 | 100% | PASS |
| `partial_match_noise` | 100.0% | 2/2 | 95% | PASS |
| `possessive` | 50.0% | 1/2 | 98% | FAIL |
| `pronoun_resolution` | 33.3% | 1/3 | 85% | FAIL |
| `site_reference` | 33.3% | 1/3 | 95% | FAIL |
| `site_shortform` | 50.0% | 1/2 | 95% | FAIL |
| `typo_resolvable` | 33.3% | 1/3 | 90% | FAIL |

## Failure Details

### `abbreviation` failures (2)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Urban Hive` | 'Urban Hive Pantry' | None |  |
| `O'Reilly` | "O'Reilly Family Orchards" | None |  |

### `alias` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Five Rivers Bee Co.` | 'Five Rivers Apiaries' | None |  |

### `apostrophe_variant` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Juniper Bee Company` | 'Juniper Bee Co.' | None |  |

### `canonical` failures (2)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Blue Ridge Farms` | 'Blue Ridge Farms' | None |  |
| `Mesa Orchard Co.` | 'Mesa Orchard Co.' | None |  |

### `casual_colloquial` failures (2)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Drakestone guys` | 'Drakestone Mead' | None |  |
| `Wenatchee family guys` | 'Wenatchee Family Orchards' | None |  |

### `compound_reference` failures (2)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Wenatchee Family Orchards river block` | 'Wenatchee Family Orchards' | None |  |
| `Martinez north block` | 'Martinez & Sons Orchards' | None |  |

### `cross_customer_collision` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Haley Farms` | 'Haley Farms LP' | None |  |

### `long_form_sentence` failures (4)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Can you log a note on the Wenatchee Family Orchards account?` | 'Wenatchee Family Orchards' | None |  |
| `schedule a hive inspection for the Mountain Valley Farms next visit` | 'Mountain Valley Farms' | None |  |
| `What queens did we ship to Goldenridge Fruit Co. last month?` | 'Goldenridge Fruit Co.' | None |  |
| `Can you pull up the account for O'Reilly Family Orchards?` | "O'Reilly Family Orchards" | None |  |

### `possessive` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Cobblestone's` | 'Cobblestone Hives' | None |  |

### `pronoun_resolution` failures (2)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Can you pull up their inspection records?` | 'Martinez & Sons Orchards' | 'Mountain Comb Co.' |  |
| `They confirmed the new contract.` | 'Sierra Crest Orchard Partners' | None |  |

### `site_reference` failures (2)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Urban Hive Pantry Main Warehouse` | 'Urban Hive Pantry' | None |  |
| `Sierra Crest Hilltop 2` | 'Sierra Crest Orchard Partners' | None |  |

### `site_shortform` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Haley orchard road site` | 'Haley Orchard Co.' | None |  |

### `typo_resolvable` failures (2)

| Input | Expected | Got | Error |
|---|---|---|---|
| `Juniper Bee Co` | 'Juniper Bee Co.' | None |  |
| `Cobblstone Hives` | 'Cobblestone Hives' | None |  |

