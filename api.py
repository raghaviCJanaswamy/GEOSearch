"""
FastAPI REST API for GEOSearch.
Exposes hybrid search (semantic + lexical + MeSH) as HTTP endpoints.

Run locally:
    uvicorn api:app --reload --port 8000

Docker: add a new service in docker-compose.prod.yml that runs this file.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from db import GSESeries, get_db
from llm.qa import generate_answer
from search.hybrid_search import HybridSearchEngine

logger = logging.getLogger(__name__)

app = FastAPI(
    title="GEOSearch API",
    description="AI-powered semantic search over NCBI GEO dataset metadata.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class DateRange(BaseModel):
    start: date | None = None
    end: date | None = None


class SearchFilters(BaseModel):
    organisms: list[str] | None = Field(None, example=["Homo sapiens"])
    tech_type: str | None = Field(None, example="Expression profiling by high throughput sequencing")
    date_range: DateRange | None = None
    min_samples: int | None = Field(None, ge=1)


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, example="breast cancer RNA-seq")
    filters: SearchFilters | None = None
    use_semantic: bool = True
    use_lexical: bool = True
    use_mesh: bool = True
    top_k: int = Field(50, ge=1, le=500)


class MeshTermMatch(BaseModel):
    mesh_id: str
    preferred_name: str
    entry_terms: list[str]
    descriptor_ui: str


class SearchMeta(BaseModel):
    query: str
    expanded_query: str
    mesh_terms: list[dict[str, Any]]
    semantic_count: int
    lexical_count: int
    total_results: int
    filters_applied: dict[str, Any]


class SearchResponse(BaseModel):
    results: list[dict[str, Any]]
    metadata: SearchMeta


class AskRequest(BaseModel):
    query: str = Field(..., min_length=1, example="What datasets exist for breast cancer single-cell RNA-seq?")
    filters: SearchFilters | None = None
    use_semantic: bool = True
    use_lexical: bool = True
    use_mesh: bool = True
    top_k: int = Field(20, ge=1, le=100)
    llm_model: str | None = Field(None, description="Override model (e.g. llama3, gpt-4o-mini)")
    llm_provider: str = Field("auto", description="'auto' | 'ollama' | 'openai' | 'none'")


class AskResponse(BaseModel):
    question: str
    answer: str
    provider: str
    source_count: int
    metadata: SearchMeta


class StatsResponse(BaseModel):
    postgres_count: int
    milvus_count: int | None
    in_sync: bool | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_filters(filters: SearchFilters | None) -> dict[str, Any]:
    """Convert Pydantic SearchFilters to the dict format HybridSearchEngine expects."""
    if filters is None:
        return {}
    result: dict[str, Any] = {}
    if filters.organisms:
        result["organisms"] = filters.organisms
    if filters.tech_type:
        result["tech_type"] = filters.tech_type
    if filters.date_range and (filters.date_range.start or filters.date_range.end):
        result["date_range"] = {
            "start": filters.date_range.start,
            "end": filters.date_range.end,
        }
    if filters.min_samples:
        result["min_samples"] = filters.min_samples
    return result


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health", tags=["System"])
def health() -> dict[str, str]:
    """Basic liveness check."""
    return {"status": "ok"}


@app.post("/search", response_model=SearchResponse, tags=["Search"])
def search_post(body: SearchRequest, db: Session = Depends(get_db)):
    """
    Hybrid search over GEO datasets.

    Combines:
    - **Semantic search** via Milvus (sentence-transformers or OpenAI embeddings)
    - **Lexical search** via PostgreSQL full-text search
    - **MeSH expansion** — medical synonyms added to query before embedding

    Results are ranked with Reciprocal Rank Fusion (RRF).
    """
    try:
        engine = HybridSearchEngine(db)
        raw = engine.search(
            query=body.query,
            filters=_build_filters(body.filters),
            use_semantic=body.use_semantic,
            use_lexical=body.use_lexical,
            use_mesh=body.use_mesh,
            top_k=body.top_k,
        )
    except Exception as exc:
        logger.exception("Search failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return SearchResponse(
        results=raw["results"],
        metadata=SearchMeta(**raw["metadata"]),
    )


@app.get("/search", response_model=SearchResponse, tags=["Search"])
def search_get(
    q: str = Query(..., description="Search query", example="breast cancer RNA-seq"),
    top_k: int = Query(50, ge=1, le=500, description="Number of results"),
    semantic: bool = Query(True, description="Enable semantic search"),
    lexical: bool = Query(True, description="Enable lexical search"),
    mesh: bool = Query(True, description="Enable MeSH query expansion"),
    organisms: list[str] | None = Query(None, description="Filter by organism(s)"),
    tech_type: str | None = Query(None, description="Filter by technology type"),
    min_samples: int | None = Query(None, ge=1, description="Minimum sample count"),
    db: Session = Depends(get_db),
):
    """
    Hybrid search via GET — convenient for quick browser/curl queries.

    Example:
        GET /search?q=breast+cancer+RNA-seq&top_k=10&organisms=Homo+sapiens
    """
    filters_obj = SearchFilters(
        organisms=organisms,
        tech_type=tech_type,
        min_samples=min_samples,
    )
    body = SearchRequest(
        query=q,
        filters=filters_obj,
        use_semantic=semantic,
        use_lexical=lexical,
        use_mesh=mesh,
        top_k=top_k,
    )
    return search_post(body, db)


@app.post("/ask", response_model=AskResponse, tags=["Q&A"])
def ask(body: AskRequest, db: Session = Depends(get_db)):
    """
    Ask a natural language question about GEO datasets.

    1. Runs hybrid search to find relevant datasets.
    2. Passes the top results as context to an LLM (OpenAI).
    3. Returns a synthesized answer grounded in the actual dataset metadata.

    Requires `OPENAI_API_KEY` in the environment for full LLM answers.
    Falls back to a structured summary if no key is configured.
    """
    from config import settings

    try:
        engine = HybridSearchEngine(db)
        raw = engine.search(
            query=body.query,
            filters=_build_filters(body.filters),
            use_semantic=body.use_semantic,
            use_lexical=body.use_lexical,
            use_mesh=body.use_mesh,
            top_k=body.top_k,
        )
    except Exception as exc:
        logger.exception("Search failed during /ask")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    results = raw["results"]

    try:
        answer, provider = generate_answer(
            question=body.query,
            results=results,
            llm_provider=body.llm_provider,
            llm_model=body.llm_model,
            openai_api_key=settings.openai_api_key,
            ollama_base_url=settings.ollama_base_url,
            ollama_model=settings.ollama_model,
        )
    except Exception as exc:
        logger.exception("LLM generation failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return AskResponse(
        question=body.query,
        answer=answer,
        provider=provider,
        source_count=len(results),
        metadata=SearchMeta(**raw["metadata"]),
    )


@app.get("/stats", response_model=StatsResponse, tags=["System"])
def stats(db: Session = Depends(get_db)):
    """
    Return record counts from PostgreSQL and Milvus.
    Useful for verifying data sync after ingestion.
    """
    pg_count = db.query(GSESeries).count()

    milvus_count: int | None = None
    try:
        from pymilvus import Collection, connections
        from config import settings

        connections.connect(host=settings.milvus_host, port=str(settings.milvus_port))
        col = Collection(settings.milvus_collection_name)
        col.load()
        milvus_count = col.num_entities
    except Exception as exc:
        logger.warning(f"Could not reach Milvus for stats: {exc}")

    in_sync = (milvus_count == pg_count) if milvus_count is not None else None

    return StatsResponse(
        postgres_count=pg_count,
        milvus_count=milvus_count,
        in_sync=in_sync,
    )


@app.get("/datasets/{accession}", tags=["Datasets"])
def get_dataset(accession: str, db: Session = Depends(get_db)):
    """
    Fetch full metadata for a single GEO dataset by accession (e.g. GSE12345).
    """
    gse = db.query(GSESeries).filter(GSESeries.accession == accession.upper()).first()
    if gse is None:
        raise HTTPException(status_code=404, detail=f"Dataset {accession} not found")
    result = gse.to_dict()
    result["geo_url"] = f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={accession.upper()}"
    return result
