"""Field apiarists (12), ops managers (3), admin (1) — 16 users total.

Deterministic: same seed produces the same roster.
"""
# pyright: reportMissingImports=false
from __future__ import annotations

import uuid

from faker import Faker
from sqlalchemy import text
from sqlalchemy.orm import Session

from seed.models import Apiarist, Region

# Fixed names so the demo has stable, pronounceable apiarists to point at.
FIELD_APIARISTS = [
    ("Isa Martinez", ["central_valley"]),
    ("Kai Okonkwo", ["central_valley", "mountain_west"]),
    ("Renée Boucher", ["pacific_northwest"]),
    ("Sam O'Reilly", ["pacific_northwest", "mountain_west"]),
    ("Priya Ramaswamy", ["mountain_west"]),
    ("Devon Hale", ["midwest"]),
    ("Luz García", ["midwest", "northeast"]),
    ("Ari Solomon", ["northeast"]),
    ("Jun-ho Park", ["northeast"]),
    ("Maya Whitfield", ["southeast"]),
    ("Theo Papadakis", ["southeast", "midwest"]),
    ("Nora Adeyemi", ["central_valley", "southeast"]),
]

OPS_MANAGERS = [
    ("Simone Delacroix", ["central_valley", "pacific_northwest"]),
    ("Marcus Okafor", ["mountain_west", "midwest"]),
    ("Elena Vasquez", ["northeast", "southeast"]),
]

ADMIN = ("Dana Chen", [])


def generate(faker: Faker, regions: list[Region]) -> list[Apiarist]:
    # faker kept in signature for consistency with other generators even
    # though this roster is fully determined; one call keeps RNG aligned.
    faker.random_int(0, 1)
    valid_codes = {r.code for r in regions}

    def _mk(idx: int, name: str, role: str, region_codes: list[str]) -> Apiarist:
        # Ensure region codes exist.
        assert all(rc in valid_codes for rc in region_codes), (
            f"unknown region code in {region_codes}"
        )
        handle = name.lower().replace("'", "").replace(" ", ".").replace("é", "e").replace("ñ", "n").replace("í", "i").replace("ó", "o").replace("ú", "u").replace("á", "a")
        return Apiarist(
            name=name,
            email=f"{handle}@meridian-apiaries.test",
            role=role,  # type: ignore[arg-type]
            erp_apiarist_no=f"APR-{idx:04d}",
            region_codes=region_codes,
        )

    out: list[Apiarist] = []
    idx = 1
    for name, regions_ in FIELD_APIARISTS:
        out.append(_mk(idx, name, "apiarist", regions_))
        idx += 1
    for name, regions_ in OPS_MANAGERS:
        out.append(_mk(idx, name, "ops_manager", regions_))
        idx += 1
    name, regions_ = ADMIN
    out.append(_mk(idx, name, "admin", regions_))
    return out


def persist(db: Session, apiarists: list[Apiarist]) -> None:
    """Insert into `users` + `territory_assignments`. Fails loudly on:
      * duplicate emails (IntegrityError from DB)
      * unknown region codes (LookupError before insert)
    """
    trows = db.execute(text("SELECT id, code FROM territories")).mappings().all()
    code_to_id = {r["code"]: r["id"] for r in trows}

    for a in apiarists:
        user_id = str(uuid.uuid4())
        db.execute(
            text(
                "INSERT INTO users (id, email, name, role, erp_salesperson_no, is_active) "
                "VALUES (:id, :email, :name, :role, :erp_no, true)"
            ),
            {
                "id": user_id,
                "email": a.email,
                "name": a.name,
                "role": a.role,
                "erp_no": a.erp_apiarist_no,
            },
        )
        for rc in a.region_codes:
            if rc not in code_to_id:
                raise LookupError(
                    f"Region code {rc!r} not found in territories table. "
                    f"Did regions.persist run? Known: {sorted(code_to_id)}"
                )
            db.execute(
                text(
                    "INSERT INTO territory_assignments (id, user_id, territory_id) "
                    "VALUES (:id, :user_id, :territory_id)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "user_id": user_id,
                    "territory_id": code_to_id[rc],
                },
            )
