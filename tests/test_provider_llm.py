"""E2E tests for the LLM provider.

Policy under test: the provider MUST fail loudly when no API key is set,
and must forward real calls when one is. There is no canned-response
fallback.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import pytest

from providers.llm import LLMClient, LLMConfigurationError


class TestNoKey:
    def test_init_raises_without_api_key(self, clean_llm_env) -> None:
        with pytest.raises(LLMConfigurationError) as excinfo:
            LLMClient()
        # Error must name both keys so the user knows how to fix it.
        assert "ANTHROPIC_API_KEY" in str(excinfo.value)
        assert "OPENAI_API_KEY" in str(excinfo.value)

    def test_no_canned_attribute_exists(self) -> None:
        """Guards against regressions — canned code must not be reintroduced."""
        import providers.llm as mod
        assert not hasattr(mod, "_canned_complete")
        assert not hasattr(mod, "CANNED_ORDER_EXTRACTION")
        assert not hasattr(mod, "CANNED_CLASSIFICATION")


class TestRealAnthropic:
    """Forwarded-call tests. Skipped if no key is set."""

    def test_simple_completion_returns_text(self, require_llm_api_key) -> None:
        client = LLMClient()
        resp = client.complete(
            prompt="Reply with exactly the word 'meridian' and no punctuation.",
            max_tokens=32,
        )
        assert resp.text.strip().lower() == "meridian"
        assert resp.usage.get("input_tokens", 0) > 0

    def test_structured_extraction_works(self, require_llm_api_key) -> None:
        client = LLMClient()
        resp = client.complete(
            prompt=(
                "Extract the hive count from this message and return ONLY "
                "the integer, no words: 'We need 400 hives for almond bloom.'"
            ),
            max_tokens=16,
        )
        digits = "".join(c for c in resp.text if c.isdigit())
        assert digits == "400"

    def test_stream_yields_chunks(self, require_llm_api_key) -> None:
        client = LLMClient()
        chunks = list(client.stream(
            "Say 'ok' and stop.",
            max_tokens=8,
        ))
        assert chunks
        assert "".join(chunks)
