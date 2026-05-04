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
        description="OpenAI chat model used when llm_provider=openai.",
    )
    anthropic_model: str = Field(
        default="claude-sonnet-4-6",
        description="Anthropic chat model used when llm_provider=anthropic.",
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide Settings singleton. Cached after first call."""
    return Settings()
