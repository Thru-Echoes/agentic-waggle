"""LLM client — real API only. No canned fallbacks.

Policy: if `ANTHROPIC_API_KEY` (preferred) or `OPENAI_API_KEY` is not set in
the environment, the client fails loudly at construction. There is no
canned-response fallback because canned responses create the illusion that
the agent works when it does not.

Public surface:

    @dataclass
    class LLMResponse:
        text: str
        tool_calls: list[dict]
        usage: dict[str, int]

    class LLMConfigurationError(RuntimeError)

    class LLMClient:
        def __init__(**kwargs)
        def complete(prompt, system=None, tools=None, **kwargs) -> LLMResponse
        def stream(prompt, **kwargs) -> Iterator[str]

    supports_temperature(model) -> bool
    get_temperature(model, temperature=0.0) -> float | None

    get_provider_error_types() -> tuple[type[Exception], ...]
    describe_provider_error(exc) -> str
    check_provider_auth() -> ProviderStatus            (one free metadata request)
    get_provider_status(ttl_seconds=300) -> ProviderStatus (cached)

Side effects: `complete` makes a real HTTP call to Anthropic or OpenAI.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterator, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    import openai


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)


class LLMConfigurationError(RuntimeError):
    """Raised when no LLM API key is configured.

    Intentional. The stack requires a real LLM; there is no canned-response
    fallback.
    """


#: Model-name prefixes known to accept an explicit ``temperature``. Newer
#: OpenAI families (gpt-5.5, gpt-5.6, gpt-6.x, the o-series) accept only the
#: provider default and reject any other value with HTTP 400
#: ("'temperature' does not support 0 with this model"). Anything not listed
#: here is sent without a temperature argument: omitting it never fails,
#: while sending it to the wrong family fails every call.
TEMPERATURE_ACCEPTING_PREFIXES: tuple[str, ...] = ("gpt-4", "gpt-5.4", "claude-")

#: Sampling temperature used for every structured-extraction call when the
#: model accepts one. Deterministic output is the goal; grounding is audited
#: separately.
DEFAULT_TEMPERATURE = 0.0


def supports_temperature(model: str) -> bool:
    """Return True when ``model`` is known to accept an explicit temperature.

    Parameters:
        model: Provider model id (for example ``gpt-6.1-terra``).

    Returns:
        True for the families in ``TEMPERATURE_ACCEPTING_PREFIXES``; False
        otherwise, including for unknown models.

    Side effects: none.
    """
    return model.startswith(TEMPERATURE_ACCEPTING_PREFIXES)


def get_temperature(model: str, temperature: float = DEFAULT_TEMPERATURE) -> float | None:
    """Return the temperature to request for ``model``, or ``None`` to omit it.

    ``None`` is what LangChain's chat models treat as "do not send"; the raw
    OpenAI client needs its ``NOT_GIVEN`` sentinel instead (see
    ``omit_if_none``).

    Parameters:
        model: Provider model id.
        temperature: Desired temperature when supported.

    Side effects: none.
    """
    return temperature if supports_temperature(model) else None


def get_openai_client() -> "openai.OpenAI":
    """Return a fresh ``openai.OpenAI`` client, raising ``LLMConfigurationError``
    when ``OPENAI_API_KEY`` is not set.

    Returns:
        Configured ``openai.OpenAI`` instance.

    Side effects: reads ``OPENAI_API_KEY`` from the environment.
    """
    import openai

    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise LLMConfigurationError(
            "OPENAI_API_KEY is not set. "
            "The semantic-matcher LLM disambiguator requires a real OpenAI key."
        )
    return openai.OpenAI(api_key=key)


def resolve_provider() -> str:
    """Return the provider name ("openai" | "anthropic") honoring settings + key presence.

    Settings-driven: `Settings.llm_provider` picks the primary. If that
    provider's key is missing, silently fall through to the other one for
    demo resilience. If *both* keys are missing, raise `LLMConfigurationError`.

    Pure-ish: the only side effect is reading env vars + calling
    `get_settings()` which caches the Settings instance.
    """
    from app.config import get_settings

    settings = get_settings()
    primary = settings.llm_provider
    fallback = "anthropic" if primary == "openai" else "openai"

    if primary == "openai" and os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if primary == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    # Primary key missing — try the other one.
    if fallback == "openai" and os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if fallback == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"

    raise LLMConfigurationError(
        "No LLM API key set. Waggle needs a real LLM. "
        f"Set OPENAI_API_KEY (primary — llm_provider={primary}) or "
        "ANTHROPIC_API_KEY in .env before running `make demo`."
    )




def _anthropic_complete(
    prompt: str,
    system: str | None,
    tools: list[dict] | None,
    **kwargs: Any,
) -> LLMResponse:
    """Call the Anthropic Messages API and coerce to an LLMResponse.

    Default model: `Settings.anthropic_model` (currently `claude-sonnet-4-6`).
    """
    import anthropic

    from app.config import get_settings

    client = anthropic.Anthropic()
    model = kwargs.pop("model", get_settings().anthropic_model)
    max_tokens = kwargs.pop("max_tokens", 1024)

    call_kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        call_kwargs["system"] = system
    if tools:
        call_kwargs["tools"] = tools
    call_kwargs.update(kwargs)

    resp = client.messages.create(**call_kwargs)

    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for block in resp.content:
        kind = getattr(block, "type", "")
        if kind == "text":
            text_parts.append(block.text)
        elif kind == "tool_use":
            tool_calls.append(
                {
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                }
            )

    return LLMResponse(
        text="".join(text_parts),
        tool_calls=tool_calls,
        usage={
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
        },
    )


def _openai_complete(
    prompt: str,
    system: str | None,
    tools: list[dict] | None,
    **kwargs: Any,
) -> LLMResponse:
    """Call the OpenAI Chat Completions API and coerce to an LLMResponse.

    Default model: `Settings.openai_model` (currently `gpt-5.4-mini`).
    """
    from app.config import get_settings

    client = get_openai_client()
    model = kwargs.pop("model", get_settings().openai_model)
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    call_kwargs: dict[str, Any] = {"model": model, "messages": messages}
    if tools:
        call_kwargs["tools"] = tools
    call_kwargs.update(kwargs)
    if "temperature" in call_kwargs and not supports_temperature(model):
        # Families that only accept the default reject the argument outright.
        call_kwargs.pop("temperature")

    # gpt-5.x family rejects ``max_tokens`` and requires
    # ``max_completion_tokens``. Translate transparently so callers can
    # keep passing the older idiom.
    if "max_tokens" in call_kwargs and "max_completion_tokens" not in call_kwargs:
        call_kwargs["max_completion_tokens"] = call_kwargs.pop("max_tokens")
    resp = client.chat.completions.create(**call_kwargs)
    choice = resp.choices[0].message
    tool_calls: list[dict[str, Any]] = []
    for tc in choice.tool_calls or []:
        tool_calls.append(
            {
                "id": tc.id,
                "name": tc.function.name,
                "input": tc.function.arguments,
            }
        )

    return LLMResponse(
        text=choice.content or "",
        tool_calls=tool_calls,
        usage={
            "input_tokens": resp.usage.prompt_tokens if resp.usage else 0,
            "output_tokens": resp.usage.completion_tokens if resp.usage else 0,
        },
    )


log = logging.getLogger(__name__)


def get_provider_error_types() -> tuple[type[Exception], ...]:
    """Return the exception classes that mean "the LLM provider failed".

    Covers configuration (no key), provider-side rejections (bad key, rate
    limit, bad request) and transport failures for both SDKs. Imported
    lazily so callers that never touch an SDK do not pay for it.

    Side effects: imports the SDK modules.
    """
    import anthropic
    import httpx
    import openai

    return (
        LLMConfigurationError,
        openai.APIError,
        anthropic.APIError,
        httpx.HTTPError,
    )


def describe_provider_error(exc: BaseException) -> str:
    """Return a user-facing sentence for a provider failure.

    Never echoes the provider message: it can carry masked key fragments,
    account URLs and request ids that belong in the server log, not in a
    browser.

    Side effects: none.
    """
    status = getattr(exc, "status_code", None)
    if isinstance(exc, LLMConfigurationError):
        return "No LLM API key is configured on this server."
    if status == 401:
        return "The LLM provider rejected this server's API key."
    if status == 429:
        return "The LLM provider is rate-limiting this server. Try again in a minute."
    if status == 404:
        return "The configured LLM model is not available to this API key."
    if isinstance(status, int) and status >= 500:
        return "The LLM provider is having an outage."
    if status is None:
        return "The LLM provider could not be reached."
    return "The LLM provider returned an error."


ProviderState = Literal["ok", "unauthorized", "unreachable", "unconfigured", "error"]


class ProviderStatus(BaseModel):
    """Result of one LLM-provider authentication probe."""

    model_config = ConfigDict(frozen=True)

    provider: str | None = Field(
        default=None, description="openai | anthropic | None when unconfigured."
    )
    model: str | None = Field(default=None, description="Model id that was probed.")
    state: ProviderState = Field(
        ..., description="ok, unauthorized, unreachable, unconfigured, or error."
    )
    detail: str = Field(
        ..., description="Fixed user-facing phrase; never the raw provider message."
    )
    checked_at: float = Field(..., description="Unix time of the probe.")

    @property
    def ok(self) -> bool:
        """True only when the key was accepted for the configured model."""
        return self.state == "ok"


def check_provider_auth() -> ProviderStatus:
    """Probe the configured provider with a free metadata request.

    ``GET /v1/models/{model}`` costs no tokens and answers the one question
    that matters at boot and in ``/ready``: does this key work for this
    model? A dead key is logged at ERROR so it is loud in the deploy logs.

    Side effects: one HTTPS request to the provider; one log line on failure.
    """
    from app.config import get_settings

    now = time.time()
    try:
        provider = resolve_provider()
    except LLMConfigurationError as exc:
        status = ProviderStatus(
            state="unconfigured", detail=describe_provider_error(exc), checked_at=now
        )
        log.error("LLM provider probe: %s", status.detail)
        return status

    settings = get_settings()
    model = settings.openai_model if provider == "openai" else settings.anthropic_model
    try:
        if provider == "openai":
            get_openai_client().models.retrieve(model)
        else:
            import anthropic

            anthropic.Anthropic().models.retrieve(model)
    except Exception as exc:  # noqa: BLE001 — classified by status below, never swallowed silently
        code = getattr(exc, "status_code", None)
        state: ProviderState
        if code in (401, 403):
            state = "unauthorized"
        elif code is None:
            state = "unreachable"
        else:
            state = "error"
        status = ProviderStatus(
            provider=provider,
            model=model,
            state=state,
            detail=describe_provider_error(exc),
            checked_at=now,
        )
        log.error("LLM provider probe failed (%s, %s): %s", provider, model, status.detail)
        return status
    return ProviderStatus(
        provider=provider,
        model=model,
        state="ok",
        detail="LLM provider reachable and key accepted.",
        checked_at=now,
    )


_PROVIDER_STATUS_CACHE: ProviderStatus | None = None


def get_provider_status(*, ttl_seconds: int = 300, force: bool = False) -> ProviderStatus:
    """Return the cached probe result, refreshing when older than ``ttl_seconds``.

    Side effects: may call ``check_provider_auth`` (one HTTPS request);
    updates the module-level cache.
    """
    global _PROVIDER_STATUS_CACHE
    cached = _PROVIDER_STATUS_CACHE
    if force or cached is None or (time.time() - cached.checked_at) > ttl_seconds:
        cached = check_provider_auth()
        _PROVIDER_STATUS_CACHE = cached
    return cached


def reset_provider_status_cache() -> None:
    """Forget the cached probe (tests). Side effect: clears module state."""
    global _PROVIDER_STATUS_CACHE
    _PROVIDER_STATUS_CACHE = None


class LLMClient:
    """Real Anthropic / OpenAI client, chosen by `Settings.llm_provider`.

    Defaults to OpenAI (gpt-5.4-mini). Falls back to the non-primary provider
    only if the primary's API key is missing at construction time; if both
    are missing, raises `LLMConfigurationError`.
    """

    def __init__(self, **_: Any) -> None:
        self._provider = resolve_provider()

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        tools: list[dict] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Return an LLMResponse. Side effect: HTTP call to the LLM provider."""
        if self._provider == "anthropic":
            return _anthropic_complete(prompt, system, tools, **kwargs)
        if self._provider == "openai":
            return _openai_complete(prompt, system, tools, **kwargs)
        raise LLMConfigurationError(f"Unknown provider {self._provider!r}")

    def stream(self, prompt: str, **kwargs: Any) -> Iterator[str]:
        """Yield ~40-char chunks of the completed text.

        Not true SSE streaming — the full completion is fetched first and
        then chunked. Good enough for HTMX/SSE demos; swap to the provider's
        native streaming API if real token-level streaming is needed.
        """
        resp = self.complete(prompt, **kwargs)
        for chunk in re.findall(r".{1,40}", resp.text):
            yield chunk
