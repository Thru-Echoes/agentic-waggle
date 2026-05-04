"""Agent-extraction request + response models."""
from __future__ import annotations

from typing import Union

from pydantic import BaseModel, ConfigDict

from agents.schemas import EmailInput, ExtractionFailure, OrderDraft


class ExtractOrderRequest(BaseModel):
    """Wraps an EmailInput so the route body is self-documenting."""

    model_config = ConfigDict(frozen=True)

    email: EmailInput


class ExtractOrderResponse(BaseModel):
    """Discriminated union of success / failure. `ok=True` ⇄ `draft` populated."""

    model_config = ConfigDict(frozen=True)

    ok: bool
    draft: OrderDraft | None = None
    failure: ExtractionFailure | None = None


ExtractResult = Union[OrderDraft, ExtractionFailure]
