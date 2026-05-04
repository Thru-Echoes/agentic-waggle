"""Waggle — FastAPI application package.

Public entry point: `app.main.app` (the FastAPI instance, constructed by
`create_app()`). Uvicorn/Docker point here.

Subpackages:
    * config   — pydantic-settings typed environment configuration
    * schemas  — request/response Pydantic models (one file per resource)
    * routes   — route modules registered at startup
    * templates — Jinja HTML (served via `app.routes.pages`)
"""
