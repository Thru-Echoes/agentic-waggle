"""First-class provider implementations for agentic-mcp-crm.

This package holds the app's real implementations for external-facing
concerns: vector search, object storage, ERP sync, inbox, outbound mail,
and LLM calls. Each provider is laptop-local — no cloud services required
— and fail-loud by construction:

    * `vector_search.py` — sentence-transformers + pgvector cosine search.
    * `storage.py`       — local filesystem object store with path-traversal
                           protection.
    * `erp.py`           — JSON-line "Hive-side ERP" mock; honest reads
                           replay honest writes.
    * `inbox.py`         — fixture-backed inbound-mail reader.
    * `mailer.py`        — outbound-mail logger.
    * `llm.py`           — real Anthropic / OpenAI only; raises
                           `LLMConfigurationError` if no API key is set.

Policy reminders (also in `.claude/rules/fail-loud.md`):

    * No canned LLM responses.
    * No silent empty returns on missing fixtures.
    * No unconditional-True stubs — if a check can't be performed, raise.
"""
