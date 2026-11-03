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
        default="postgresql+psycopg2://agentic_mcp_crm:agentic_mcp_crm@postgres:5432/agentic_mcp_crm"
    )
    redis_url: str = Field(default="redis://redis:6379/0")

    # -- LLM --------------------------------------------------------------
    # Waggle defaults to OpenAI gpt-6.1-terra for every stage whose output a
    # user reads. On the 2026-11-02 extraction benchmark
    # (docs/benchmarks/2026-11-02-extract-models.md) it matched the cheaper
    # families on accuracy (25/25) and was the smallest model to ground
    # 100% of its evidence quotes verbatim in the source on all three
    # stages; gpt-6.1-sol showed no measured gain at twice the price.
    # Anthropic is kept as a toggle for demo resilience but `llm_provider`
    # decides which one actually runs.
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
        default="gpt-6.1-terra",
        description=(
            "Default OpenAI chat model when no per-stage override is set. "
            "Used by LLMClient (MCP tools, the demo script) and as the "
            "fallback for any stage without its own field."
        ),
    )
    anthropic_model: str = Field(
        default="claude-sonnet-4-6",
        description="Anthropic chat model used when llm_provider=anthropic.",
    )

    # -- Per-stage model assignments (driven by the benchmark) ------------
    # See docs/benchmarks/2026-11-02-extract-models.md (and the 2026-05-08
    # report it supersedes). Every candidate hit 100% accuracy on these
    # stages; the deciding metric is the share of evidence quotes that
    # appear verbatim in the source email, because those quotes are what
    # the UI renders as "Source" tooltips and Evidence blocks. One model is
    # kept across the three stages: the report's per-stage picks come from a
    # 10-case sample, and a tie there is not enough to split the extractor.
    extract_intent_model: str = Field(
        default="gpt-6.1-terra",
        description=(
            "Model for the intent-classification stage. 100% accuracy and "
            "100% verbatim evidence on the benchmark (gpt-5.4-mini: 0%)."
        ),
    )
    extract_customer_model: str = Field(
        default="gpt-6.1-terra",
        description=(
            "Model for the customer-reference extraction stage. The quoted "
            "evidence is shown next to the matched customer, so grounding "
            "matters (100% vs 20% for gpt-5.4-mini). gpt-6-luna also "
            "grounded 100% here on the 10-case sample at a tenth of the "
            "price; terra is kept so the extractor runs one model."
        ),
    )
    extract_items_model: str = Field(
        default="gpt-6.1-terra",
        description=(
            "Model for the line-items extraction stage. Line-item evidence "
            "is rendered as user-facing 'Source' tooltips; terra grounded "
            "100% of quotes (gpt-5.4-nano 90%, gpt-5.4-mini 10%)."
        ),
    )

    # -- Per-stage model assignments for the sales copilot ----------------
    # Each stage gets the cheapest model that hits the task's accuracy bar.
    # Routes / titler use the fast intent classifier; main answer-generation
    # reuses the items-extract model (highest verbatim-grounding rate at
    # low cost); report extraction uses the customer-extract model.
    copilot_router_model: str = Field(
        default="gpt-6.1-terra",
        description=(
            "Intent classifier for one chat turn. Single call per turn; "
            "classification + 1-2 evidence quotes (same profile as the "
            "extraction intent stage)."
        ),
    )
    copilot_main_model: str = Field(
        default="gpt-6.1-terra",
        description=(
            "Main answer-generation model. Answers are user-facing and must "
            "stay grounded in the retrieved context."
        ),
    )
    copilot_report_model: str = Field(
        default="gpt-6.1-terra",
        description=(
            "Report-extraction model for log_customer_report intent. Same "
            "profile as customer-reference extraction — short structured "
            "fields with evidence."
        ),
    )
    copilot_title_model: str = Field(
        default="gpt-6-luna",
        description=(
            "Small model for auto-titling a new conversation from its first "
            "user turn. One call, ~10 tokens output; grounding is irrelevant "
            "so the cheapest current tier is enough."
        ),
    )
    copilot_matcher_disambig_model: str = Field(
        default="gpt-5.4-mini",
        description=(
            "OpenAI chat model for the semantic matcher's Stage B "
            "disambiguator. Chosen by tests/test_matcher_eval.py, not by "
            "the extraction benchmark; see STATUS.md for the current eval."
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
    waggle_demo_credentials_public: bool = Field(
        default=False,
        description=(
            "When true, the HTML 401 page prints the configured Basic-auth "
            "credentials. Only for a demo whose credentials are published on "
            "purpose (the portfolio does this); never for a real deployment."
        ),
    )
    waggle_secret_key: str | None = Field(
        default=None,
        description=(
            "HMAC key for the visitor cookie that scopes copilot conversations. "
            "Unset: a random per-process key is used, so visitors are forgotten "
            "on restart (fine locally). Set a long random value in production."
        ),
    )
    waggle_portfolio_url: str | None = Field(
        default="https://thru-echoes.github.io/waggle",
        description="Link shown on the 401 page and in the footer; None hides it.",
    )

    # -- Operational switches ---------------------------------------------
    waggle_internal_routes_enabled: bool = Field(
        default=False,
        description=(
            "Mount the /_internal/render/* template-rendering routes. They "
            "exist for template tests and must stay off in production."
        ),
    )
    llm_probe_ttl_seconds: int = Field(
        default=300,
        ge=10,
        description=(
            "How long /ready caches the LLM-provider auth probe. The probe is "
            "a free metadata request, not a completion."
        ),
    )
    waggle_llm_rate_per_minute: int = Field(
        default=10,
        ge=0,
        description=(
            "Model-backed requests allowed per client address per minute "
            "(token bucket with the same burst). 0 disables the check."
        ),
    )
    waggle_llm_daily_budget: int = Field(
        default=500,
        ge=0,
        description=(
            "Model-backed requests allowed per UTC day across all clients. "
            "0 disables the check. Each request is 2-10 model calls."
        ),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide Settings singleton. Cached after first call."""
    return Settings()
