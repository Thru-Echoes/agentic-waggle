# Eval Report: matcher_eval_sku

**Generated:** 2026-05-13 15:49 UTC  
**Git SHA:** `db2a4b0d`  
**Corpus SHA:** `df2d7b1dc0fc`  
**Eval mode:** `sample`  
**Result:** FAILURES PRESENT  

Overall precision: **83.3%** (15/18)

## Per-Category Results

| Category | Precision | Pass/Total | Threshold | Status |
|---|---|---|---|---|
| `sku_ambiguous` | 50.0% | 1/2 | 95% | FAIL |
| `sku_canonical_code` | 100.0% | 2/2 | 100% | PASS |
| `sku_colloquial` | 66.7% | 2/3 | 90% | FAIL |
| `sku_negative` | 100.0% | 2/2 | 100% | PASS |
| `sku_product_name` | 100.0% | 4/4 | 98% | PASS |
| `sku_qty_plus_product` | 66.7% | 2/3 | 98% | FAIL |
| `sku_service` | 100.0% | 2/2 | 95% | PASS |

## Failure Details

### `sku_ambiguous` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `queen excluder` | kind=None code=None | kind='product' code='EQ-EXCL-8F' |  |

### `sku_colloquial` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `clover honey drum` | kind='product' code='HONEY-CLV-A-DRUM' | kind='product' code='HONEY-CLV-B-DRUM' |  |

### `sku_qty_plus_product` failures (1)

| Input | Expected | Got | Error |
|---|---|---|---|
| `5 drums of clover honey Grade A` | kind='product' code='HONEY-CLV-A-DRUM' | kind='product' code='HONEY-WLD-A-DRUM' |  |

