# UI Verification

Standing requirement for any change that touches a user-facing surface.
This rule complements `e2e-testing.md` (data + dep policy) by adding a
**two-layer execution-based verification** for the UI itself. Reading the
rendered HTML is not verification. Clicking once manually is not
verification. Both layers below must pass before a UI change is "done."

## When this applies

- New or modified Jinja template
- New or modified page route returning HTML or HTMX partial
- Shared layout component change (re-test every page that uses it)
- User-visible copy change
- Static asset change visible in the rendered page
- Backend route change that alters what a user sees or can do

## When this does NOT apply

- Backend-only refactors with no template / route output change
- Internal tooling under `scripts/`, `seed/`, `db/`
- Documentation under `docs/` or `.claude/`
- Test-only changes

## Layer 1 — Per-clickable subagent walk

For every link, button, form submit, HTMX trigger, dropdown, tab, or other
interactive element on each affected page:

- Spawn **one subagent per element** using the Agent tool. Run subagents in
  parallel when their actions are independent (a click that doesn't depend
  on another element's state).
- Each subagent uses the Playwright MCP tools (`browser_navigate`,
  `browser_snapshot`, `browser_click`, `browser_type`,
  `browser_take_screenshot`, etc.) to:
  1. Navigate to the page in question
  2. Perform the action on the target element
  3. Observe the resulting state (URL change, swapped fragment, error text,
     visible elements, network requests)
  4. Return a structured pass/fail report with the observed evidence
- The parent agent compares each subagent's observation against the
  documented expected behavior. If any element fails or behaves
  unexpectedly, the change is not verified.
- Subagent prompts must include: the page URL, the selector or
  accessibility name of the element, and the precise expected behavior.

## Layer 2 — Playwright + pytest E2E coverage

Every UI-affecting change must come with new or updated automated tests
that meet all of the following:

- Run via pytest using real Playwright (no mocked browser, no fake DOM).
- Use real data per `e2e-testing.md` — real Postgres + pgvector, real
  filesystem, real LLM when a key is set.
- Cover the **happy path** and at least the following edge cases:
  - Empty state (no rows)
  - Loading / in-flight state where applicable (HTMX swap mid-request)
  - Error state (4xx/5xx response)
  - Full state (many rows, pagination boundaries)
- Cover **unusual event sequences** that real users might trigger:
  - Double-clicking a submit
  - Navigating away mid-load
  - Submitting forms with malformed input
  - Re-firing an HTMX trigger before its prior response returns
  - Browser back/forward across HTMX-modified pages
- Type-checked with `pyright`. Test code is not exempt.

## Reporting (claim of "done")

A UI change is not complete until both layers are green:

1. **Layer 1 report** — table of `page → element → result → evidence`
   from the subagent walk. Cite the screenshot or DOM snapshot for any
   non-trivial element.
2. **Layer 2 report** — pytest output showing all relevant E2E tests pass.
   Include the test file paths and the count.

Both reports go in the PR description (for branches) or session summary
(for direct work).

## Anti-patterns

| Don't | Do |
|---|---|
| "I clicked it once and it worked" | Spawn a subagent per element |
| "Looks fine in the rendered HTML" | Execute the click in a real browser |
| "I'm sure it works, skipping subagents" | Always run Layer 1 |
| "Layer 1 passed, no need for E2E" | Always run Layer 2 too — they catch different things |
| "Tests pass locally" | When the change is deployed, verify against the deployed environment too |
| "One generic test for the whole page" | Per-element / per-flow specificity |

## Why two layers

Layer 1 catches what's wrong **right now** with surgical precision —
which specific button on which specific page misbehaves and how. Layer 2
catches **regressions** the next time someone changes the code. Without
Layer 1, regressions slip in for weeks before anyone notices. Without
Layer 2, you re-verify everything from scratch on every change. Both
together = fast feedback now + durable safety net.
