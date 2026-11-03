# Eval Report: matcher_eval_sku

**Generated:** 2026-11-03 01:11 UTC  
**Git SHA:** `9f1d28bd`  
**Corpus SHA:** `27d19a606e66`  
**Eval mode:** `full`  
**Result:** FAILURES PRESENT  

Overall precision: **97.2%** (69/71)

## Per-Category Results

| Category | Precision | Pass/Total | Threshold | Status |
|---|---|---|---|---|
| `sku_ambiguous` | 85.7% | 6/7 | 95% | FAIL |
| `sku_canonical_code` | 100.0% | 10/10 | 100% | PASS |
| `sku_colloquial` | 100.0% | 11/11 | 90% | PASS |
| `sku_negative` | 87.5% | 7/8 | 100% | FAIL |
| `sku_product_name` | 100.0% | 17/17 | 98% | PASS |
| `sku_qty_plus_product` | 100.0% | 12/12 | 98% | PASS |
| `sku_service` | 100.0% | 6/6 | 95% | PASS |

## Failure Details

### `sku_ambiguous` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `raw honey jar` | kind=None code=None | kind=None code=None |  |

### `sku_negative` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `drone comb frame` | kind=None code=None | kind='product' code='EQ-FRAME-DP' |  |

