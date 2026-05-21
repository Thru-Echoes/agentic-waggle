# Eval Report: matcher_eval_sku

**Generated:** 2026-05-19 21:25 UTC  
**Git SHA:** `f52debb5`  
**Corpus SHA:** `27d19a606e66`  
**Eval mode:** `full`  
**Result:** FAILURES PRESENT  

Overall precision: **95.8%** (68/71)

## Per-Category Results

| Category | Precision | Pass/Total | Threshold | Status |
|---|---|---|---|---|
| `sku_ambiguous` | 71.4% | 5/7 | 95% | FAIL |
| `sku_canonical_code` | 100.0% | 10/10 | 100% | PASS |
| `sku_colloquial` | 100.0% | 11/11 | 90% | PASS |
| `sku_negative` | 87.5% | 7/8 | 100% | FAIL |
| `sku_product_name` | 100.0% | 17/17 | 98% | PASS |
| `sku_qty_plus_product` | 100.0% | 12/12 | 98% | PASS |
| `sku_service` | 100.0% | 6/6 | 95% | PASS |

## Failure Details

### `sku_ambiguous` failures (2)

| Input | Expected | Got | Error |
|---|---|---|---|
| `varroa treatment` | kind=None code=None | kind=None code=None |  |
| `honey drum` | kind=None code=None | kind=None code=None |  |

### `sku_negative` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `entrance reducer` | kind=None code=None | kind='product' code='EQ-FEED-ENT' |  |

