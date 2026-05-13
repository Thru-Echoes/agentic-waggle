# Eval Report: matcher_eval_sku

**Generated:** 2026-05-13 17:57 UTC  
**Git SHA:** `cdd4c7ab`  
**Corpus SHA:** `df2d7b1dc0fc`  
**Eval mode:** `sample`  
**Result:** FAILURES PRESENT  

Overall precision: **88.9%** (16/18)

## Per-Category Results

| Category | Precision | Pass/Total | Threshold | Status |
|---|---|---|---|---|
| `sku_ambiguous` | 100.0% | 2/2 | 95% | PASS |
| `sku_canonical_code` | 100.0% | 2/2 | 100% | PASS |
| `sku_colloquial` | 66.7% | 2/3 | 90% | FAIL |
| `sku_negative` | 100.0% | 2/2 | 100% | PASS |
| `sku_product_name` | 100.0% | 4/4 | 98% | PASS |
| `sku_qty_plus_product` | 66.7% | 2/3 | 98% | FAIL |
| `sku_service` | 100.0% | 2/2 | 95% | PASS |

## Failure Details

### `sku_colloquial` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `clover honey drum` | kind='product' code='HONEY-CLV-A-DRUM' | kind=None code=None |  |

### `sku_qty_plus_product` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `5 drums of clover honey Grade A` | kind='product' code='HONEY-CLV-A-DRUM' | kind=None code=None |  |

