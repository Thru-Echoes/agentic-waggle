"""Vector-search route.

Delegates to `providers.vector_search.VectorSearchProvider`. The provider
executes a cosine-similarity query against the `chunks` table, embedding
the request query via sentence-transformers on the way in.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.schemas.search import SearchHit, SearchRequest, SearchResponse
from db.session import get_db_session
from providers.vector_search import VectorSearchProvider

router = APIRouter(tags=["search"])


@router.post("/search", response_model=SearchResponse)
def search(
    request: SearchRequest,
    db: Session = Depends(get_db_session),
) -> SearchResponse:
    provider = VectorSearchProvider(db=db)
    filters = {
        "site_id": str(request.site_id) if request.site_id else None,
        "visit_date_after": request.visit_date_after,
    }
    results = provider.search(request.query, top_k=request.top_k, filters=filters)
    return SearchResponse(
        query=request.query,
        hits=[
            SearchHit(
                chunk_id=r.chunk_id,
                content=r.content,
                score=r.score,
                metadata=r.metadata,
            )
            for r in results
        ],
    )
