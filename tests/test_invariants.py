"""Cross-cutting guards for the rules in docs/INVARIANTS.md.

Each test enumerates the sites of one invariant so a new violation fails
here rather than in review.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from app.main import create_app
from app.ratelimit import is_llm_backed

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Functions whose call means "this request spends model tokens".
_MODEL_ENTRYPOINTS = {
    "run_order_extraction",
    "get_copilot_response",
    "resume_with_customer",
    "draft_order_from_text",
    "log_customer_report",
    "_llm_title",
}


def _handler_calls_model(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")
            if name in _MODEL_ENTRYPOINTS:
                return True
    return False


def _route_handlers_calling_the_model() -> set[str]:
    """Names of route handler functions (in app/routes) that call the model."""
    out: set[str] = set()
    helpers_calling: set[str] = set()
    modules = sorted((REPO_ROOT / "app" / "routes").glob("*.py"))
    trees = {m: ast.parse(m.read_text()) for m in modules}
    # helpers that call the model, so handlers calling those helpers count too
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _handler_calls_model(node):
                helpers_calling.add(node.name)
    targets = _MODEL_ENTRYPOINTS | helpers_calling
    for tree in trees.values():
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            is_route = any(
                isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                and d.func.attr in ("get", "post", "put", "patch", "delete")
                for d in node.decorator_list
            )
            if not is_route:
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    f = sub.func
                    name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")
                    if name in targets:
                        out.add(node.name)
                        break
    return out


def test_every_model_calling_route_is_rate_limited(monkeypatch) -> None:
    monkeypatch.setenv("WAGGLE_SKIP_PROVIDER_PROBE", "1")
    from app.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    calling = _route_handlers_calling_the_model()
    assert calling, "expected at least one model-calling route (extract, copilot turns)"
    unguarded: list[str] = []
    for route in app.routes:
        endpoint = getattr(route, "endpoint", None)
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if endpoint is None or endpoint.__name__ not in calling:
            continue
        for method in methods:
            if method in ("GET", "HEAD", "OPTIONS"):
                continue
            if not is_llm_backed(method, path):
                unguarded.append(f"{method} {path} ({endpoint.__name__})")
    get_settings.cache_clear()
    assert unguarded == [], f"model-calling routes outside LLM_BACKED_PATH_PREFIXES: {unguarded}"


_INLINE_HANDLER = re.compile(r"\son[a-z]+\s*=\s*[\"']", re.IGNORECASE)
_EVAL_FILTER = re.compile(r"hx-trigger\s*=\s*[\"'][^\"']*\[")


_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.DOTALL)


def test_templates_have_no_inline_handlers_or_eval_filters() -> None:
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "app" / "templates").rglob("*.html")):
        src = _JINJA_COMMENT.sub("", path.read_text())  # comments may mention the rule
        if _INLINE_HANDLER.search(src) or _EVAL_FILTER.search(src) or "hx-vals='js:" in src or "hx-on" in src:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == [], f"inline handlers / eval filters are blocked by the CSP: {offenders}"
