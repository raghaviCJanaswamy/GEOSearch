"""
FastAPI REST API for GEOSearch.
Exposes hybrid search (semantic + lexical + MeSH) as HTTP endpoints.

Run locally:
    uvicorn api:app --reload --port 8000

Docker: add a new service in docker-compose.prod.yml that runs this file.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from db import GSESeries, get_db
from expression.archs4 import get_archs4
from llm.qa import generate_answer
from search.hybrid_search import HybridSearchEngine

logger = logging.getLogger(__name__)

app = FastAPI(
    title="GEO Smart Search -  API",
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
    use_judge: bool = Field(False, description="Run LLM relevance judge on top results (slower, adds tier/reason per result)")
    top_k: int = Field(50, ge=1, le=500)
    expression_genes: str | None = Field(None, description="Comma-separated gene symbols to filter expression TSV, e.g. FOXP3,CD8A")


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
    judge_used: bool = False
    relevant_count: int = 0
    borderline_count: int = 0
    not_relevant_count: int = 0
    unjudged_count: int = 0
    judge_coverage: int = 0


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


@app.post("/search", tags=["Search"])
def search_post(
    body: SearchRequest,
    format: str | None = Query(None, description="Response format: omit=JSON, 'tsv'=TSV-only stream, 'multipart'=JSON+TSV in one response"),
    db: Session = Depends(get_db),
):
    """
    Hybrid search over GEO datasets.

    Combines:
    - **Semantic search** via Milvus (sentence-transformers or OpenAI embeddings)
    - **Lexical search** via PostgreSQL full-text search
    - **MeSH expansion** — medical synonyms added to query before embedding

    Results are ranked with Reciprocal Rank Fusion (RRF).

    **Response format** (via `?format=` query param):
    - *(omitted)* — JSON search results with expression summary links per dataset
    - `tsv` — expression matrices for all matched datasets as a streaming TSV attachment
    - `multipart` — single response with JSON results (part 1) + TSV attachment (part 2)

    Optionally filter genes with `expression_genes` in the request body (e.g. `"expression_genes": "FOXP3,CD8A"`).
    """
    try:
        engine = HybridSearchEngine(db)
        raw = engine.search(
            query=body.query,
            filters=_build_filters(body.filters),
            use_semantic=body.use_semantic,
            use_lexical=body.use_lexical,
            use_mesh=body.use_mesh,
            use_judge=body.use_judge,
            top_k=body.top_k,
        )
    except Exception as exc:
        logger.exception("Search failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    fmt = (format or "").lower()

    gene_filter = (
        {g.strip().upper() for g in body.expression_genes.split(",")}
        if body.expression_genes
        else None
    )

    archs4 = get_archs4()

    def _iter_expression_tsv(results, gene_filter):
        """Yield TSV rows for all results that have expression data in ARCHS4."""
        header_written = False
        for r in results:
            acc = r["accession"]
            try:
                for line in archs4.iter_expression_tsv(acc, gene_filter=gene_filter):
                    if not header_written and line.startswith("accession\t"):
                        yield line
                        header_written = True
                    elif not line.startswith("accession\t"):
                        yield line
            except KeyError:
                continue

    # --- TSV-only stream mode ---
    if fmt == "tsv":
        tsv_filename = f"geosearch_expression_{body.query[:30].replace(' ', '_')}.tsv"
        return StreamingResponse(
            _iter_expression_tsv(raw["results"], gene_filter),
            media_type="text/tab-separated-values",
            headers={"Content-Disposition": f'attachment; filename="{tsv_filename}"'},
        )

    # --- Annotate results with expression metadata (used by JSON and multipart) ---
    # One H5 scan for all accessions instead of one scan per result
    accessions = [r["accession"] for r in raw["results"]]
    expr_info_map = archs4.batch_gse_info(accessions)
    for r in raw["results"]:
        acc = r["accession"]
        info = expr_info_map.get(acc)
        if info:
            r["has_expression"] = True
            r["expression_summary"] = {
                "gene_count": info["gene_count"],
                "sample_count": info["sample_count"],
                "file_format": info["file_format"],
                "expression_url": f"/expression/{acc}",
                "download_url": f"/expression/{acc}/download",
            }
        else:
            r["has_expression"] = False
            r["expression_summary"] = None

    search_response = SearchResponse(
        results=raw["results"],
        metadata=SearchMeta(**raw["metadata"]),
    )

    # --- Multipart mode: JSON part + TSV attachment in one response ---
    if fmt == "multipart":
        boundary = uuid.uuid4().hex
        json_bytes = search_response.model_dump_json().encode()
        tsv_filename = f"geosearch_expression_{body.query[:30].replace(' ', '_')}.tsv"

        def generate_multipart():
            # Part 1: JSON search results
            yield (
                f"--{boundary}\r\n"
                f'Content-Disposition: inline; name="results"\r\n'
                f"Content-Type: application/json\r\n\r\n"
            ).encode()
            yield json_bytes
            yield b"\r\n"

            # Part 2: TSV expression matrices
            yield (
                f"--{boundary}\r\n"
                f'Content-Disposition: attachment; name="expression"; filename="{tsv_filename}"\r\n'
                f"Content-Type: text/tab-separated-values\r\n\r\n"
            ).encode()
            for chunk in _iter_expression_tsv(raw["results"], gene_filter):
                yield chunk.encode()
            yield b"\r\n"

            yield f"--{boundary}--\r\n".encode()

        return StreamingResponse(
            generate_multipart(),
            media_type=f"multipart/mixed; boundary={boundary}",
        )

    # --- Default JSON mode ---
    return search_response


@app.get("/search", tags=["Search"])
def search_get(
    q: str = Query(..., description="Search query", example="breast cancer RNA-seq"),
    top_k: int = Query(50, ge=1, le=500, description="Number of results"),
    semantic: bool = Query(True, description="Enable semantic search"),
    lexical: bool = Query(True, description="Enable lexical search"),
    mesh: bool = Query(True, description="Enable MeSH query expansion"),
    judge: bool = Query(False, description="Run LLM relevance judge (slower, adds tier/reason per result)"),
    organisms: list[str] | None = Query(None, description="Filter by organism(s)"),
    tech_type: str | None = Query(None, description="Filter by technology type"),
    min_samples: int | None = Query(None, ge=1, description="Minimum sample count"),
    expression_genes: str | None = Query(None, description="Comma-separated gene symbols to filter expression TSV, e.g. FOXP3,CD8A"),
    format: str | None = Query(None, description="'tsv' for TSV-only stream, 'multipart' for JSON+TSV in one response"),
    db: Session = Depends(get_db),
):
    """
    Hybrid search via GET — convenient for quick browser/curl queries.

    Example:
        GET /search?q=breast+cancer+RNA-seq&top_k=10&organisms=Homo+sapiens
        GET /search?q=breast+cancer&format=tsv&expression_genes=FOXP3,CD8A
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
        use_judge=judge,
        top_k=top_k,
        expression_genes=expression_genes,
    )
    return search_post(body, format=format, db=db)


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


# ---------------------------------------------------------------------------
# Expression matrix endpoints — backed by ARCHS4 H5 file
# ---------------------------------------------------------------------------

@app.get("/expression", tags=["Expression"])
def list_expression():
    """List all datasets available in the ARCHS4 H5 file is not enumerable.

    Returns metadata about the H5 source instead.
    """
    archs4 = get_archs4()
    return {
        "source": "ARCHS4",
        "gene_count": len(archs4.gene_symbols),
        "note": "Query /expression/{accession} for a specific GSE.",
    }


@app.get("/expression/{accession}", tags=["Expression"])
def get_expression(
    accession: str,
    genes: str | None = Query(None, description="Comma-separated gene symbols to filter, e.g. FOXP3,CD8A"),
    samples: str | None = Query(None, description="Comma-separated GSM IDs to filter"),
):
    """
    Return the expression matrix for a dataset directly from the ARCHS4 H5 file.

    - **accession**: GEO accession, e.g. GSE98386
    - **genes**: optional comma-separated gene symbols to return (all genes if omitted)
    - **samples**: optional comma-separated GSM IDs to return (all samples if omitted)

    Response shape:
        { accession, genes: [...], samples: [...], matrix: [[...], ...] }
    """
    acc = accession.upper()
    gene_filter = {g.strip().upper() for g in genes.split(",")} if genes else None
    sample_filter = {s.strip() for s in samples.split(",")} if samples else None

    try:
        return get_archs4().get_expression(acc, gene_filter=gene_filter, sample_filter=sample_filter)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No expression data found in ARCHS4 for {acc}")


@app.get("/expression/{accession}/download", tags=["Expression"])
def download_expression(
    accession: str,
    genes: str | None = Query(None, description="Comma-separated gene symbols to filter"),
    samples: str | None = Query(None, description="Comma-separated GSM IDs to filter"),
):
    """
    Download the expression matrix as a TSV file, read directly from the ARCHS4 H5 file.

    - **accession**: GEO accession, e.g. GSE98386
    - **genes**: optional comma-separated gene symbols (all genes if omitted)
    - **samples**: optional comma-separated GSM IDs (all samples if omitted)
    """
    acc = accession.upper()
    gene_filter = {g.strip().upper() for g in genes.split(",")} if genes else None
    sample_filter = {s.strip() for s in samples.split(",")} if samples else None

    archs4 = get_archs4()
    if not archs4.has_gse(acc):
        raise HTTPException(status_code=404, detail=f"No expression data found in ARCHS4 for {acc}")

    filename = f"{acc}_expression.tsv"
    return StreamingResponse(
        archs4.iter_expression_tsv(acc, gene_filter=gene_filter, sample_filter=sample_filter),
        media_type="text/tab-separated-values",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
