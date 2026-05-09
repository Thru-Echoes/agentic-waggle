"""Typed environment configuration for Waggle.

Uses `pydantic-settings` so every runtime setting is declared with a type
annotation, a default (or `...` for required), and lives in one place.
Reads values from the process environment (falling back to `.env` in dev).

Anti-patterns prevented here:
    * scattered `os.environ.get(...)` calls across modules
    * silently-missing required values (Pydantic raises at load time)
    * untyped string flags that drift from their declared shape
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Load via `get_settings()`.

    Side effects: reads environment variables + optional `.env` file on
    first instantiation.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # -- Branding / display -----------------------------------------------
    product_name: str = Field(default="Waggle")
    tagline: str = Field(
        default="The agentic CRM for the people who move the bees."
    )
    company_name: str = Field(default="Meridian Apiaries & Pollination Services")

    # -- Database ----------------------------------------------------------
    database_url: str = Field(
        default="postgresql://agentic_mcp_crm:agentic_mcp_crm@postgres:5432/agentic_mcp_crm"
    )
    redis_url: str = Field(default="redis://redis:6379/0")

    # -- LLM --------------------------------------------------------------
    # Waggle defaults to OpenAI gpt-5.4-mini — optimized for structured
    # extraction, fast, cheap per call, and supports OpenAI's native
    # structured-outputs feature that LangChain's `with_structured_output`
    # uses under the hood. Anthropic is kept as a toggle for demo resilience
    # but `llm_provider` decides which one actually runs.
    #
    # Keys are optional at settings load — `providers/llm.py` raises at
    # client construction if the selected provider's key is missing, so the
    # fail-loud test paths can run without global env coupling.
    anthropic_api_key: str | None = Field(default=None)
    openai_api_key: str | None = Field(default=None)
    llm_provider: Literal["openai", "anthropic"] = Field(
        default="openai",
        description="Which LLM provider powers the agent + MCP + follow-up email.",
    )
    openai_model: str = Field(
        default="gpt-5.4-mini",
        description=(
            "Default OpenAI chat model when no per-stage override is set. "
            "Acts as a fallback for code paths that don't yet read a "
            "per-stage field (e.g. MCP tools, future copilot work)."
        ),
    )
    anthropic_model: str = Field(
        default="claude-sonnet-4-6",
        description="Anthropic chat model used when llm_provider=anthropic.",
    )

    # -- Per-stage model assignments (driven by the benchmark) ------------
    # See docs/benchmarks/2026-05-08-extract-models.md for the rationale.
    # Each stage gets the cheapest model that hits 100% accuracy at its
    # complexity level, with evidence-grounding as a tie-breaker for
    # stages whose output is rendered to the user.
    extract_intent_model: str = Field(
        default="gpt-5.4-mini",
        description=(
            "Model for the intent-classification stage. Fast model — "
            "task is binary-ish and 100% accurate across all benchmarked candidates."
        ),
    )
    extract_customer_model: str = Field(
        default="gpt-5.4-mini",
        description=(
            "Model for the customer-reference extraction stage. Fast model "
            "— output feeds a deterministic fuzzy match, so paraphrasing "
            "in evidence is acceptable."
        ),
    )
    extract_items_model: str = Field(
        default="gpt-5.4-nano",
        description=(
            "Model for the line-items extraction stage. Nano variant chosen "
            "because line-item evidence is rendered as user-facing 'Source' "
            "tooltips and nano hit 90% verbatim-grounding in benchmark "
            "(vs 0% for gpt-5.4-mini)."
        ),
    )
    extract_resolver_fallback_model: str = Field(
        default="gpt-5.5",
        description=(
            "Model for the LLM-fallback SKU resolver. Rare path (only when "
            "deterministic fuzzy match misses) so the latency cost is "
            "acceptable; gpt-5.5 chosen for highest evidence-grounding "
            "rate (100%) on stages this complex."
        ),
    )

    # -- Provider filesystem roots ----------------------------------------
    storage_root: Path = Field(default=Path("/local-storage"))
    sent_mail_root: Path = Field(default=Path("/sent-mail"))
    erp_log_root: Path = Field(default=Path("/erp-sync-log"))
    inbox_fixture_root: Path = Field(default=Path("/gmail-fixtures"))

    # -- App server -------------------------------------------------------
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=5001, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO"
    )

    # -- HTTP Basic auth (production gate) --------------------------------
    # When BOTH are set, every request (except /health, /ready, /static/*)
    # requires HTTP Basic credentials matching this pair. Either unset →
    # middleware is a no-op. Set via Fly secrets in production; leave
    # blank for local dev.
    waggle_user: str | None = Field(
        default=None,
        description="HTTP Basic auth username for production gate.",
    )
    waggle_pass: str | None = Field(
        default=None,
        description="HTTP Basic auth password for production gate.",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide Settings singleton. Cached after first call."""
    return Settings()
