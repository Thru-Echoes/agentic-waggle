"""Single-LLM-call intent router for the sales copilot.

Classifies a chat-turn input into one of:

    query | log_customer_report | create_order | command | unknown

Returns a ``IntentRoute(Grounded)`` carrying verbatim-quote evidence
and a self-rated confidence. Backend-only data — confidence is not
rendered to the user.

The router uses the model assigned by ``Settings.copilot_router_model``
(populated from the benchmark; falls back to ``extract_intent_model``).
One call per chat turn; no tool use; no DB.

Side effects: one LLM call.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from agents.copilot.conversation_schemas import IntentRoute


_PROMPT_TEMPLATE = """\
Classify this user message from a sales copilot.

Message:
{text}

Pick exactly one intent:

- query                  : the user is asking a question to retrieve information
                           (e.g. "what's the varroa trend at O'Reilly?",
                           "summarize my last visit to Cascade").
- log_customer_report    : the user is dictating or typing a visit / trip report
                           (e.g. "Visited Cascade today — mites at 3.5%, queens
                           healthy, follow up next week.").
- create_order           : the user is asking to draft a new order
                           (e.g. "Create an order for St. Agnes — 40 hives.").
- command                : the user is asking to change a specific field
                           (price, status, address, etc).
- unknown                : none of the above clearly fits.

Fields to fill:
- intent: one of the categories above
- target_account_hint: verbatim customer / company name from the message,
  if any. Otherwise null.
- reasoning: one short sentence
- evidence: 1-2 verbatim quotes from the message that justify your choice
  (raw text, no surrounding quotation marks)
- confidence: 0.0-1.0 self-rated
"""


def _build_router_model() -> BaseChatModel:
    """Build the chat model assigned to the copilot router stage."""
    from agents.order_extractor import _build_chat_model
    from app.config import get_settings

    settings = get_settings()
    # Fall back to extract_intent_model until we add a copilot-specific
    # field — both stages have the same task profile (fast classification).
    model_name = getattr(
        settings, "copilot_router_model", settings.extract_intent_model
    )
    return _build_chat_model(model_name)


def get_intent_route(
    text: str,
    *,
    model: BaseChatModel | None = None,
) -> IntentRoute:
    """Classify ``text`` into an ``IntentRoute``.

    Pure-ish function — no DB, no state. One LLM call, returns the parsed
    ``IntentRoute``. The orchestrator passes ``model`` when it has one
    cached; otherwise we build a fresh one.

    Side effects: one LLM call.
    """
    chat_model = model or _build_router_model()
    structured = chat_model.with_structured_output(IntentRoute)
    prompt = _PROMPT_TEMPLATE.format(text=text)
    result: IntentRoute = structured.invoke(prompt)  # type: ignore[assignment]
    return result
