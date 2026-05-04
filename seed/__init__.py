"""Synthetic Meridian Apiaries seed generator.

Each module in this package produces a list of Pydantic domain objects.
`generate.py` is the entry point that orchestrates generation order
(respecting FK dependencies) and persists through a SQLAlchemy session.

Run from inside the seed container:

    python -m seed.generate            # seed (skips if data exists)
    python -m seed.generate --drop-existing   # wipe and regenerate
"""
