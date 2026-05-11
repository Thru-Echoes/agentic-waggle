# Copilot Frontend — Phase F Design (2026-05-10)

> *Pick up from commit e8190df: backend orchestrator + 4-tool pipeline +
> chat_turns persistence are done. Build the user-facing chat surface and
> wire it end-to-end.*

## Goals

1. **A working `/copilot` page** — modern 3-pane chat (rail + thread + composer),
   server-rendered with Jinja, HTMX-driven for in-place turn updates.
2. **End-to-end persistence** — every chat turn (user + assistant) writes a
   row to `chat_turns`; conversations show in the rail and survive page
   reloads.
3. **Confirm-and-persist for drafts** — when the orchestrator returns an
   `OrderDraft` or `CustomerReportDraft`, the assistant turn renders an
   inline draft card with a Confirm button. Confirm hits a route that
   writes the actual `orders` row (with state transition) or
   `customer_reports` row.
4. **Two-layer UI verification** — every interactive element on `/copilot`
   gets a Layer 1 subagent walk; a Playwright pytest suite covers the
   happy path + edge cases (Layer 2).

## Non-goals (explicit YAGNI)

- **Streaming responses** — full HTTP request/response with an HTMX
  in-flight indicator. Streaming is a future polish; would need SSE
  plumbing and a different backend handler shape.
- **Audio dictation (Whisper)** — schema reserves `audio_blob_id` on
  `ChatTurnIn` and the form will accept it inert; backend transcription
  lives in a future phase.
- **Multi-user auth** — keep the existing `BasicAuthMiddleware` story for
  Fly. No per-user conversations yet — `salesperson_id` stays nullable
  and unset.
- **Inline editing of draft fields** — the Confirm button persists the
  draft as-extracted. Edit-before-persist is a follow-up.

## Architecture

```
                 ┌─────────────────────────────────────────────┐
   GET /copilot  │   templates/copilot.html (Jinja shell)      │
                 │   ├─ partials/copilot_rail.html             │
                 │   ├─ partials/copilot_thread.html           │
                 │   │   ├─ copilot_turn_user.html             │
                 │   │   └─ copilot_turn_assistant.html        │
                 │   │       ├─ copilot_draft_order.html       │
                 │   │       └─ copilot_draft_report.html      │
                 │   └─ <composer form>                        │
                 └─────────────────────────────────────────────┘
                                       ↑
                                       │ HTMX swaps
                                       │
   POST /copilot/turns                 │
   POST /copilot/conversations         │   (route layer in
   POST /copilot/turns/{id}/confirm-…  │    app/routes/copilot.py)
   GET  /copilot/rail                  │
                                       ↓
                  agents/copilot/orchestrator.py (already built)
                                       ↓
                  Postgres (conversations + chat_turns)
```

### Routes

| Method | Path                                         | Purpose                                       | Returns        |
|--------|----------------------------------------------|-----------------------------------------------|----------------|
| GET    | `/copilot`                                   | Render shell, auto-create or pick latest conv | Full HTML      |
| GET    | `/copilot/{conversation_id}`                 | Render shell anchored on a specific conv      | Full HTML      |
| POST   | `/copilot/turns`                             | Create user turn → run orchestrator → persist assistant turn → render both partials | HTMX partial |
| POST   | `/copilot/conversations`                     | Create new empty conversation                 | 303 redirect   |
| POST   | `/copilot/turns/{turn_id}/confirm-order`     | Persist a stashed `OrderDraft` to `orders`    | HTMX partial (success card) |
| POST   | `/copilot/turns/{turn_id}/confirm-report`    | Persist a stashed `CustomerReportDraft`       | HTMX partial (success card) |
| GET    | `/copilot/rail`                              | Re-render the conversation list (post-update) | HTMX partial   |
| GET    | `/api/copilot/conversations`                 | JSON list (programmatic clients)              | JSON           |
| GET    | `/api/copilot/conversations/{id}`            | JSON conversation read (programmatic)         | JSON           |

### Draft stash mechanism

The orchestrator currently returns a `CopilotResponse` with `text` + tool-call
records. To make Confirm round-trippable, we extend `CopilotResponse` with two
optional fields:

- `pending_order_draft: OrderDraft | None`
- `pending_report_draft: CustomerReportDraft | None`

The route handler stores the draft (as a JSON-serialized dict) inside the
assistant `ChatTurn.tool_calls` JSONB list as a synthetic record with
`tool_name="pending_draft"`. The Confirm endpoint reads the turn, finds the
synthetic record, validates it back into the original schema, and persists.

This keeps drafts co-located with the turn that produced them — no orphan
table to clean up, no separate migration.

### Account context

A small "active customer" indicator sits above the composer. The orchestrator
already carries `account_index` + per-turn auto-resolution. When a turn lands
with a resolved customer that differs from the current `active_account_id`,
the route updates `Conversation.active_account_id` and HTMX swaps the
indicator. The user can also click an X to clear the active account.

### Conversation auto-titling

After the first user turn, the route calls a tiny LLM helper to summarize
the message into a 4-6 word title and writes it to `Conversation.title`.
Falls back to the first 60 chars of the user's first message if no LLM key.
(Not a separate route — runs inline at the end of the first POST.)

## Data flow per turn

```
User types in composer
    ↓
POST /copilot/turns  (form: text, conversation_id)
    ↓
Route handler:
    1. Insert ChatTurn(role="user", text=...)
    2. Build account_index (or reuse cached one)
    3. Call get_copilot_response(...)
    4. Insert ChatTurn(role="assistant", text=response.text,
                       tool_calls=response.tool_calls + maybe pending_draft,
                       evidence_used=..., model=..., tokens=..., latency_ms=...)
    5. Update Conversation.updated_at + active_account_id
    6. If first turn: title the conversation
    7. Render: partials/copilot_turn_user.html + partials/copilot_turn_assistant.html
       (HTMX swap appends both into #thread)
```

## Templates + styling

- **base.html** — add `Copilot` between `Inbox` and `Customers` in the nav.
- **copilot.html** — extends base, 3-pane layout: rail (collapsible on
  mobile), thread (scroll container), composer (sticky bottom).
- **partials/copilot_thread.html** — wrapper that renders all turns.
- **partials/copilot_turn_user.html** — right-aligned bubble, comb-color,
  small timestamp.
- **partials/copilot_turn_assistant.html** — left-aligned bubble, white
  with subtle border, footer chips: model name, latency, tool calls,
  citation chips clickable to underlying records, evidence expander
  (`<details>`).
- **partials/copilot_draft_order.html** — embedded inside the assistant
  bubble; reuses the order-fields shape from `partials/agent_draft.html`,
  adds a `Confirm & save` button + status badge.
- **partials/copilot_draft_report.html** — similar; site, varroa, observations,
  action items + Confirm.
- **partials/copilot_rail.html** — list of conversations with title,
  most-recent-message preview, last-touched timestamp; "+ New conversation"
  button at top.
- **partials/copilot_indicator.html** — pill with a pulsing dot, swapped
  in by HTMX during the in-flight POST.

CSS additions in `app/static/waggle.css`:
- `.copilot` 3-column grid (`240px 1fr` on tablet+; single-column on mobile).
- `.thread`, `.turn`, `.turn.user`, `.turn.assistant`, `.turn .meta`,
  `.turn .tool-chip`, `.turn .citation-chip`, `.turn details.evidence`.
- `.composer` sticky-bottom with auto-grow textarea (vanilla — no JS lib).
- `.draft-card` for inline order/report drafts.
- `.in-flight` pulse animation.

## Settings additions (`app/config.py`)

```
copilot_router_model: str = Field(default=..., description="Fast intent classifier.")
copilot_main_model:   str = Field(default=..., description="Main answer-generation model.")
copilot_report_model: str = Field(default=..., description="Report-extraction model.")
copilot_title_model:  str = Field(default=..., description="Conversation auto-title model (cheapest).")
```

Defaults pull from the existing per-stage benchmark — router/title use the
fast intent model, main uses the items model (already grounded), report uses
the customer-extract model.

## Tests (per `.claude/rules/e2e-testing.md`)

### Backend route E2E (`tests/test_routes_copilot.py`)

- `test_copilot_page_renders_empty_state` — GET /copilot → 200, contains nav + composer + empty thread message.
- `test_create_conversation_redirects_to_new_id` — POST /copilot/conversations → 303 + Location header.
- `test_post_turn_query_persists_both_turns` — POST /copilot/turns, check chat_turns row count = 2, intent records show up in tool_calls.
- `test_post_turn_create_order_stashes_draft` — POST a "draft order for X" message, check assistant turn's tool_calls contains a pending_draft synthetic record.
- `test_confirm_order_persists_orders_row` — POST /copilot/turns/{id}/confirm-order → orders row exists with state=DRAFT (or EXTRACTED), order_state_transitions row written.
- `test_confirm_report_persists_customer_reports_row` — same shape for reports.
- `test_post_turn_log_report_extracts` — log_customer_report intent stashes a CustomerReportDraft.
- `test_rail_lists_conversations_newest_first` — multiple conversations, newest at top.
- `test_account_auto_resolution_updates_conversation` — turn referencing a known customer updates Conversation.active_account_id.
- `test_post_turn_fails_loud_without_llm` — when LLM key absent, 500 with explicit error (no silent fallback).

### Playwright E2E (`tests/test_e2e_copilot_browser.py`)

Real browser via Playwright. Each test starts uvicorn against the test DB,
opens /copilot, performs the user flow, asserts on the rendered DOM:

- happy path — type a question, see assistant reply appear, click citation chip → navigates.
- create-order flow — request a draft, see card, click Confirm → success badge, orders row exists in DB.
- log-report flow — same shape for reports.
- empty state — fresh DB has no conversations, page shows the prompt and the composer is enabled.
- conversation switch — multiple conversations in rail, click switches, thread re-renders.
- double-submit guard — clicking Send twice rapidly does not produce two user turns.
- network failure — kill the server mid-turn, error toast appears, composer re-enabled.
- mobile collapse — narrow viewport collapses the rail behind a hamburger.

### Layer 1 — per-clickable subagent walk

Spawn one subagent per element on `/copilot`, in parallel where independent:

1. Send button (composer submit)
2. Conversation rail item click
3. + New conversation button
4. Confirm-order button (on a draft card)
5. Confirm-report button (on a draft card)
6. Citation chip click (each kind: customer_report, order, email, customer, product)
7. Evidence `<details>` expander
8. Active-account "X" clear button
9. Mobile rail toggle (if at narrow width)

Each subagent uses Playwright MCP to navigate, perform the action, snapshot,
return pass/fail with evidence. Aggregate into the table for the session
summary.

### Verification budget

- `pyright` clean on every modified file.
- `pytest tests/test_routes_copilot.py` 100% pass with real LLM key set.
- `pytest tests/test_e2e_copilot_browser.py` 100% pass (skip with explicit
  message if Playwright + browser not installed; documented in CI notes).
- `make test` full suite still 100% (no regression in existing 187 passing).

## Out-of-scope items deferred to later

- **Streaming responses** (Server-Sent Events on the POST /copilot/turns)
- **Inline draft editing** (edit fields before Confirm)
- **Audio dictation** (Whisper transcription endpoint)
- **Multi-user auth + per-user conversations** (right now `salesperson_id`
  stays null; the basic-auth gate is the only credential)
- **Soft-delete conversations** (no Delete button in the rail yet)
- **Search across conversation history** (rail is just newest-first)

These are all clean future-phase additions — the data model already supports
them; the wiring just isn't built.
