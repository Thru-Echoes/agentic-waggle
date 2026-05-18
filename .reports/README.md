# Eval reports — committed so PR diffs show quality deltas

Matcher eval reports are written here by `pytest tests/test_matcher_eval.py`.

Files in this directory are committed to the repo so that PR diffs surface
quality changes between runs. Each report records the git SHA and corpus SHA
at time of generation, making regressions attributable to specific commits.

## Files

- `matcher_eval_customer.md` — per-category precision for all customer-matching categories
- `matcher_eval_sku.md` — per-category precision for all SKU-matching categories

## Running the eval

Sample mode (default, 20% stratified, ~5–10 min):

```bash
TEST_DATABASE_URL="postgresql://agentic_mcp_crm:agentic_mcp_crm@localhost:5433/agentic_mcp_crm_test" \
  MATCHER_EVAL_MODE=sample \
  pytest tests/test_matcher_eval.py -v
```

Full corpus mode:

```bash
TEST_DATABASE_URL="postgresql://agentic_mcp_crm:agentic_mcp_crm@localhost:5433/agentic_mcp_crm_test" \
  MATCHER_EVAL_FULL=1 \
  pytest tests/test_matcher_eval.py -v
```
