---
description: Code quality standards for agentic-mcp-crm — functional style preferred, no unnecessary complexity
globs: ["seed/**/*.py", "providers/**/*.py", "app/**/*.py", "agents/**/*.py", "mcp/**/*.py", "db/**/*.py", "tests/**/*.py"]
---

# Code Quality

These standards match the user's conventions across REAP, TRACE, carbon-pulse, and green-narrative. When in doubt, read `~/Documents/Berkeley/Research/REAP/.claude/rules/code-quality.md` for the canonical reference.

## Style — functional first

- **Prefer pure functions** with explicit inputs and outputs over classes with implicit state
- Use classes **only when state is genuinely needed** (e.g., wrapping a DB session, a cached model, a stateful ERP client)
- **Three clear lines beat one clever abstraction** — no unnecessary complexity
- Favor clarity over cleverness — name variables descriptively (`customer_id_lookup`, not `lookup`)
- Functions should have **one clear purpose** — split when they grow past ~50 lines
- Prefer numpy / comprehensions over hand-rolled Python loops where readability permits

## Naming Conventions (use consistently)

- `get_*` — retrieve, compute, or construct data
- `set_*` — configure or mutate state (document the side effect)
- `compute_*` — derive a metric or measurement
- `run_*` — execute a pipeline or multi-step workflow
- `find_*` — search or optimize
- `build_*` — assemble a complex structure
- `validate_*` — check data quality, raise on failure
- `persist_*` / `_persist` — write to an external store (used in seed generators)
- Private helpers: `_lowercase_with_underscores`

## Data Models

- **Pydantic v2 BaseModel** for every structured value that crosses a function boundary — no raw dicts
- `Field(..., description="...")` on all public-facing fields
- Strict types where correctness matters: `StrictStr`, `Literal`, `conint`, `confloat`
- `model_validate()` / `model_dump()` — never manual dict construction
- For truly immutable data, use `ConfigDict(frozen=True)` (adopt where it doesn't conflict with current code)
- `Decimal` for money — never `float`

## Documentation

- **Every function gets a docstring** — no exceptions
- Docstrings state: what it does, parameters, return, **side effects** (if any — writes to disk? mutates state? calls an API? say so)
- Module-level docstring at the top of each file explaining the module's purpose and public exports
- No inline comments unless the WHY is genuinely non-obvious
- Don't narrate the code — if a reader can read the code to see WHAT, they don't need a comment

## Logging

- `logging.getLogger(__name__)` — never `print()`
- Log at meaningful boundaries (pipeline start/end, errors, slow operations)
- Do not log sensitive data (API keys, customer PII)

## Type Safety

- Type hints on every public function
- Run `pyright` against this directory before considering work done
- `from __future__ import annotations` at the top of every file (for forward refs + cleaner union syntax)
- Do not add `# type: ignore` without a reason comment
- Avoid `Any` in core/public interfaces — prefer precise types, `Literal`, `Enum`, discriminated unions

## Boundary Discipline

- Validate at every boundary — raw dicts must not drift into core logic
- Celery tasks: `Model.model_dump(mode="json")` at producer, `Model.model_validate(payload)` at consumer
- Prefer JSON serialization; avoid pickle

## Anti-patterns (forbidden unless justified per-file)

- Class hierarchies when functions + data models suffice
- Raw `dict[str, Any]` as a function parameter or return (use Pydantic)
- Magic numbers — use named constants at module level
- `from X import *` anywhere
- Mutating a function argument in place (prefer returning a new value)
