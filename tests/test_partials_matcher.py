"""Confirm-match + pick-match partials render with the expected
elements and forms.

These tests hit the internal render routes (/_internal/render/confirm-match
and /_internal/render/pick-match) which have no DB dependency — they only
render Jinja templates. No DB fixture is needed; the client fixture here
does not override get_db_session.
"""
from __future__ import annotations

import uuid
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    """FastAPI TestClient for template-only routes (no DB override needed)."""
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_confirm_match_partial_renders_yes_no_form(client: TestClient) -> None:
    """Confirm-match partial contains customer name, hidden customer_id, and both buttons.

    Jinja2 HTML-escapes apostrophes to &#39; — assertions use the escaped form.
    """
    cid = str(uuid.uuid4())
    tid = str(uuid.uuid4())
    resp = client.get(
        f"/_internal/render/confirm-match?turn_id={tid}&customer_id={cid}"
        f"&customer_name=O%27Reilly%20Family%20Orchards"
    )
    assert resp.status_code == 200
    body = resp.text
    # Jinja2 auto-escapes ' → &#39; in HTML context.
    assert "O&#39;Reilly Family Orchards" in body
    assert 'name="customer_id"' in body
    assert 'value="' + cid + '"' in body
    assert "Yes" in body and "No" in body


def test_pick_match_partial_renders_alternatives(client: TestClient) -> None:
    """Pick-match partial renders all alternative customer names and IDs.

    Jinja2 HTML-escapes apostrophes to &#39; — assertions use the escaped form.
    """
    cid1 = str(uuid.uuid4())
    cid2 = str(uuid.uuid4())
    resp = client.get(
        f"/_internal/render/pick-match?turn_id={uuid.uuid4()}&"
        f"alt={cid1}|O%27Reilly%20Family%20Orchards&"
        f"alt={cid2}|O%27Connell%20Brothers"
    )
    assert resp.status_code == 200
    body = resp.text
    # Jinja2 auto-escapes ' → &#39; in HTML context.
    assert "O&#39;Reilly Family Orchards" in body
    assert "O&#39;Connell Brothers" in body
    assert cid1 in body and cid2 in body
