"""Regulatory reference documents — LLM-authored compliance summaries.

5 documents covering distinct regulatory regimes (USDA-APHIS Tropilaelaps
quarantine, CDFA apiary movement, FDA VFD oxytetracycline, EPA formic
acid label, TTB COLA analog for mead). Each is persisted as a
`documents` row with `source_type='regulatory_doc'`.

`source_id` is a fresh UUID per row — these documents have no foreign
domain row, but the column is non-null, so we mint one and use it as
a stable handle.

Reads from `seed/fixtures/regulatory_docs.json`.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "regulatory_docs.json"


class FixtureMissing(FileNotFoundError):
    """Raised when seed/fixtures/regulatory_docs.json is missing."""


def _load_fixture() -> list[dict]:
    if not FIXTURE_PATH.exists():
        raise FixtureMissing(
            f"Regulatory-doc fixture missing at {FIXTURE_PATH}. "
            f"Generate it via `python -m scripts.generate_fixtures`."
        )
    return json.loads(FIXTURE_PATH.read_text())


def persist(db: Session) -> int:
    """Insert one `documents` row per regulatory doc. Returns count.

    Side effects: SQL INSERT into `documents`.
    """
    docs = _load_fixture()
    for d in docs:
        db.execute(
            text(
                "INSERT INTO documents "
                "(id, source_type, source_id, content, metadata) "
                "VALUES (:id, 'regulatory_doc', :src_id, :content, "
                "        CAST(:meta AS jsonb))"
            ),
            {
                "id": str(uuid.uuid4()),
                "src_id": str(uuid.uuid4()),  # synthetic; no FK target
                "content": f"{d['title']}\n\n{d['content']}",
                "meta": json.dumps(
                    {
                        "source_type": "regulatory_doc",
                        "code": d["code"],
                        "title": d["title"],
                        "scope": d["scope"],
                    }
                ),
            },
        )
    return len(docs)
