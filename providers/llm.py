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

Side effects: `complete` makes a real HTTP call to Anthropic or OpenAI.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterator


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
    import openai

    from app.config import get_settings

    client = openai.OpenAI()
    model = kwargs.pop("model", get_settings().openai_model)
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    call_kwargs: dict[str, Any] = {"model": model, "messages": messages}
    if tools:
        call_kwargs["tools"] = tools
    call_kwargs.update(kwargs)

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
