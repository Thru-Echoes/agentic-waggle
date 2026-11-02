"""Synthetic data uses reserved ``.test`` domains only.

Guard for every fixture and seed source: a registrable domain in demo data
reads like a real company's address, and the data is meant to be
unmistakably fictional.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_ALLOWED_SUFFIXES = (".test", ".example", ".invalid", ".localhost", ".local")


def _sources() -> list[Path]:
    files: list[Path] = []
    files += sorted((REPO_ROOT / "seed" / "fixtures").glob("*.json"))
    files += sorted((REPO_ROOT / "gmail-fixtures").glob("*.inbox.json"))
    files += sorted((REPO_ROOT / "seed").glob("*.py"))
    return files


def test_every_email_domain_in_synthetic_data_is_reserved() -> None:
    offenders: dict[str, set[str]] = {}
    for path in _sources():
        for domain in _EMAIL.findall(path.read_text()):
            if not domain.lower().endswith(_ALLOWED_SUFFIXES):
                offenders.setdefault(str(path.relative_to(REPO_ROOT)), set()).add(domain)
    assert offenders == {}, f"registrable domains in synthetic data: {offenders}"


def test_seed_email_timestamps_are_deterministic(faker_instance) -> None:
    """Two generate() calls yield identical received_at values (fixed clock)."""
    from faker import Faker

    from seed import customers as customers_gen
    from seed import emails as emails_gen
    from seed import regions as regions_gen
    from seed import apiarists as apiarists_gen
    from seed.faker_providers import BeekeepingProvider

    def _run():
        fk = Faker(locale="en_US")
        Faker.seed(42)
        fk.add_provider(BeekeepingProvider)
        regions = regions_gen.generate()
        customers = customers_gen.generate(fk)
        apiarists = apiarists_gen.generate(fk, regions)
        return [e.received_at for e in emails_gen.generate(fk, customers, apiarists)]

    first, second = _run(), _run()
    assert first == second
    assert all(ts <= emails_gen.SEED_CLOCK_ANCHOR for ts in first)
