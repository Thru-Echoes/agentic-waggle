---
description: E2E testing standards for agentic-mcp-crm — real dependencies, no vacuous assertions, no mocks
globs: ["tests/**/*.py"]
---

# E2E Testing Rules

Reference pattern: the user applies equivalent rules in REAP (`~/Documents/Berkeley/Research/REAP/.claude/rules/e2e-testing.md`), carbon-pulse, green-narrative.

## Philosophy

Every deliverable must verify itself end-to-end against real dependencies. Unit tests are necessary but not sufficient — the interesting failures live at the boundaries (DB schema, FK ordering, Pydantic validation, LLM responses, embedding dims). E2E tests catch what unit tests miss.

## No mocks — real dependencies

- **pgvector** — use a real Postgres with the `vector` extension, not an in-memory SQLite shim
- **sentence-transformers** — use the real `all-MiniLM-L6-v2` model; first run downloads ~80 MB, that's OK
- **Filesystem** — use `tmp_path` fixtures pointing at real filesystem; no fake FS abstractions
- **LLM** — use a real Anthropic / OpenAI API when the key is present; explicitly skip with a documented message when it is not (fixture `require_llm_api_key`)

**No mocks for any of the above.** If a test can't run without the real dep, it fails loudly until that dep is available. `pytest.importorskip()` is acceptable for truly optional deps; `unittest.mock` / `MagicMock` is not acceptable for primary deps.

## No vacuous tests

A test is vacuous if:
- It would pass even if the function body were `pass` or `return None` or an empty list
- It asserts the tested function returns its own constants back (test and code share the same source of truth)
- It only checks `len(x) > 0` on output that's guaranteed to be non-empty by construction

**Test the contract, not the implementation.** Examples:

Bad (passes if generator returns all one state):
```python
states = {o.state for o in orders}
assert "DRAFT" in states
```

Good (fails if distribution is wrong):
```python
by_state = Counter(o.state for o in orders)
for state, expected_count in STATE_DISTRIBUTION:
    assert by_state[state] == expected_count
```

Bad (tautological — generator iterates ENTRIES and returns the count):
```python
assert len(items) == len(ENTRIES)
```

Good (asserts downstream invariants that could drift):
```python
assert all(i.restriction_type in {"state_import_permit", "vfd_required", "epa_label_restriction"} for i in items)
assert {i.product_sku for i in items if i.product_sku} <= {p.sku for p in products}
```

## Fail-loud test assertions

- Every `persist()` has a "duplicate insertion raises IntegrityError" test
- Every FK-consuming persist has a "missing X raises LookupError" test
- Every required environment variable has a "missing env raises" test
- Negative-case tests must actually reach the negative path — `pytest.raises(...)` with `match=...` to pin the error message

## Test isolation

- Fresh DB schema per test via `db` fixture (per-test schema drop + recreate). Transactional-rollback isolation is OK too, but fresh-schema is stricter.
- No shared state between tests via module-level variables
- `tmp_path`-based fixtures for anything filesystem-touching
- `monkeypatch.setattr` for overriding module constants; revert happens automatically

## Coverage

- **Every public function** in `seed/`, `providers/`, `agents/`, `mcp/`, `app/` has at least one E2E test
- Every provider has tests covering: successful path, missing-config path, missing-fixture path, and (where applicable) real-API-backed forwarding
- Integration test (`test_integration_full_seed.py`) runs the complete `seed.generate.run()` pipeline and asserts exact row counts across every table

## Running

```bash
make test          # full suite against the test DB; real embeddings; LLM if key set
make test-unit     # Pydantic model validation only (no DB required)
```
