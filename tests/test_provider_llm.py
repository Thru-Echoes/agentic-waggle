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


# ---------------------------------------------------------------------------
# Temperature capability rule (no network)
# ---------------------------------------------------------------------------


class TestTemperatureRule:
    """Newer OpenAI families reject any explicit temperature with HTTP 400;
    the provider must omit the argument for them and keep it for the
    families that accept it."""

    def test_known_families_accept_temperature(self) -> None:
        from providers.llm import get_temperature, supports_temperature

        for model in ("gpt-4o-mini", "gpt-5.4-mini", "gpt-5.4-nano", "claude-sonnet-4-6"):
            assert supports_temperature(model), model
            assert get_temperature(model) == 0.0

    def test_default_only_families_get_no_temperature(self) -> None:
        from providers.llm import get_temperature, supports_temperature

        for model in ("gpt-6.1-terra", "gpt-6.1-sol", "gpt-6-luna", "gpt-5.6-luna", "gpt-5.5", "o3-mini"):
            assert not supports_temperature(model), model
            assert get_temperature(model) is None

    def test_unknown_model_is_treated_as_default_only(self) -> None:
        from providers.llm import get_temperature

        assert get_temperature("some-future-model") is None

    def test_explicit_temperature_is_passed_through_when_supported(self) -> None:
        from providers.llm import get_temperature

        assert get_temperature("gpt-5.4-mini", temperature=0.7) == 0.7
