---
description: Fail-loud policy — no silent drops, no canned fallbacks, no fake-success paths
globs: ["seed/**/*.py", "providers/**/*.py", "app/**/*.py", "agents/**/*.py", "mcp/**/*.py", "db/**/*.py", "tests/**/*.py"]
---

# Fail-Loud Policy

This project enforces fail-loud semantics throughout. A silent failure is worse than a loud one because it makes the code **look** like it works when it doesn't.

## Hard rules (grep-enforced where possible)

1. **No `ON CONFLICT DO NOTHING`** — duplicate inserts must raise `IntegrityError`. Regression guard: `grep -rn "ON CONFLICT" seed/` must return zero hits (docstrings excepted).
2. **No silent FK skips** — `if x is None: continue` in a persistence loop is forbidden. Raise `LookupError` with the specific name that failed to resolve. Example:
   ```python
   if customer_id is None:
       raise LookupError(
           f"Site references unknown customer {s.customer_name!r}. "
           f"Did customers.persist run first?"
       )
   ```
3. **No try/except that swallows** — the only permitted `except Exception` pattern is the transactional session wrapper (rollback + **re-raise**). Log-and-continue is forbidden.
4. **No canned LLM responses** — the LLM either works with a real API key or raises `LLMConfigurationError` at init. No "if no key, return fake output." Regression guard: `test_no_canned_attribute_exists` in `tests/test_override_llm.py` asserts the forbidden names do not exist.
5. **No fake padding** — if the curated list has 14 entries, the generator returns 14, not 25 with placeholders. Tests pin the exact count.
6. **No unconditional-true stubs** — `sage_stub.customer_exists` must query the DB, not return `True` unconditionally. If a stub cannot verify (no session passed in), raise.
7. **No silent empty-return on missing fixtures** — the inbox provider raises `InboxFixtureMissing` when the inbox file is absent; it does not return `[]`.

## Exception types — pick the right one

- **`LookupError`** — unresolved reference (unknown customer name, unknown SKU, unknown region code)
- **`ValueError`** — input fails validation (empty line_items, negative total, non-numeric value)
- **`RuntimeError`** — environmental failure (session not provided, model returned wrong dim)
- **`IntegrityError`** (SQLAlchemy) — DB constraint violation; let it propagate, do not catch
- **`FileNotFoundError`** / subclass — expected file missing; subclass it for clarity (e.g., `InboxFixtureMissing`)
- Domain-specific: define your own (`StorageKeyError`, `LLMConfigurationError`) with clear messages

## Error messages — always actionable

Bad:
```python
raise LookupError("not found")
```

Good:
```python
raise LookupError(
    f"Order references unknown customer {o.customer_name!r}. "
    f"Did customers.persist run first?"
)
```

Every error message must: name what was looked for, name where it was expected, suggest the likely fix.

## Tests are part of the enforcement

Every fail-loud claim is tested:
- `test_duplicate_X_raises_integrity_error` for every `persist()`
- `test_missing_FK_raises_lookup_error` for every FK-consuming persist
- `test_init_raises_without_api_key` for LLM stub
- `test_missing_fixture_file_raises_loudly` for Gmail stub
- `test_exact_count_matches_curated_list` for regulated items (no padding)

When adding new code, write the fail-loud test alongside the code. If the error path is not tested, it doesn't exist.
