"""LLM-provider auth probe and the readiness surface that reports it.

The probe is a free metadata request. Without any key it must report
``unconfigured``; with a key present it performs one real request.
"""
from __future__ import annotations

import pytest

from providers import llm


def test_probe_reports_unconfigured_without_keys(clean_llm_env: None) -> None:
    llm.reset_provider_status_cache()
    status = llm.check_provider_auth()
    assert status.state == "unconfigured"
    assert status.ok is False
    assert "No LLM API key" in status.detail


def test_probe_cache_respects_ttl_and_force(clean_llm_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    llm.reset_provider_status_cache()
    first = llm.get_provider_status(ttl_seconds=3600)
    again = llm.get_provider_status(ttl_seconds=3600)
    assert again is first  # cached
    forced = llm.get_provider_status(ttl_seconds=3600, force=True)
    assert forced is not first


def test_probe_reports_unauthorized_for_a_bad_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """One real metadata request with a syntactically valid but bogus key."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-not-a-real-key-for-the-waggle-probe-test")
    llm.reset_provider_status_cache()
    status = llm.check_provider_auth()
    assert status.state in ("unauthorized", "unreachable")
    assert "sk-proj" not in status.detail


def test_probe_ok_with_real_key(require_llm_api_key: None) -> None:
    llm.reset_provider_status_cache()
    status = llm.check_provider_auth()
    assert status.state == "ok", status.detail


def test_describe_provider_error_never_echoes_the_message() -> None:
    import httpx2
    import openai

    request = httpx2.Request("POST", "https://api.openai.com/v1/chat/completions")
    exc = openai.AuthenticationError(
        "Error code: 401 - Incorrect API key provided: sk-proj-secret",
        response=httpx2.Response(401, request=request),
        body=None,
    )
    text = llm.describe_provider_error(exc)
    assert "sk-proj" not in text and "rejected" in text
    assert "rate-limit" in llm.describe_provider_error(
        openai.RateLimitError("slow down", response=httpx2.Response(429, request=request), body=None)
    )
    assert "No LLM API key" in llm.describe_provider_error(llm.LLMConfigurationError("x"))
