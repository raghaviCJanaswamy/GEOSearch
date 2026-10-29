"""GEOSearch MCP server.

Exposes two tools for AI agents:
  - search_geo     — hybrid search (semantic + lexical + MeSH) over 263K+ GEO series
  - get_dataset    — fetch full metadata for a single GSE accession

Transport: Streamable HTTP (stateless), mounted at /mcp.

Run standalone:
    uvicorn mcp_server:app --port 8090

Or alongside the FastAPI app via docker-compose by adding a service that runs:
    uvicorn mcp_server:app --host 0.0.0.0 --port 8090 --workers 2

Claude Desktop config (~/.claude/claude_desktop_config.json):
    {
      "mcpServers": {
        "geosearch": {
          "type": "http",
          "url": "http://localhost:8090/mcp"
        }
      }
    }
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.routing import Route

from db import GSESeries, SessionLocal
from search.hybrid_search import HybridSearchEngine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MCP server instance
# ---------------------------------------------------------------------------

mcp_server = MCPServer(
    "geosearch",
    instructions=(
        "Search GEOSearch — an AI-powered index of over 263,000 NCBI GEO series "
        "(GSE accessions) spanning human, mouse, rat, drosophila, zebrafish, "
        "arabidopsis, yeast, and C. elegans. "
        "Use search_geo to find datasets by topic in plain scientific language: "
        "include organism, assay type, disease, tissue, cell type, or perturbation "
        "when they matter. Results are ranked by hybrid semantic + lexical + MeSH "
        "relevance. Use get_dataset to fetch full metadata for a specific GSE."
    ),
)

# Fields returned per result in search_geo (keep tokens reasonable for agents)
BRIEF_FIELDS = (
    "accession", "title", "summary", "organism", "tech_type",
    "submission_date", "sample_count", "pubmed_id",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_db() -> Session:
    db = SessionLocal()
    try:
        return db
    except Exception:
        db.close()
        raise


def _search(query: str, top_k: int, use_mesh: bool, use_judge: bool = True) -> list[dict]:
    db = SessionLocal()
    try:
        engine = HybridSearchEngine(db)
        raw = engine.search(
            query=query,
            filters={},
            use_semantic=True,
            use_lexical=True,
            use_mesh=use_mesh,
            use_judge=use_judge,
            top_k=top_k,
        )
        results = []
        for r in raw["results"]:
            brief = {k: r.get(k) for k in BRIEF_FIELDS}
            brief["geo_url"] = f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={r['accession']}"
            brief["relevance_tier"] = r.get("tier", "unjudged")
            brief["relevance_reason"] = r.get("reason", "")
            results.append(brief)
        return results
    finally:
        db.close()


def _fetch_dataset(accession: str) -> dict:
    db = SessionLocal()
    try:
        gse = db.query(GSESeries).filter(
            GSESeries.accession == accession.upper()
        ).first()
        if gse is None:
            raise HTTPException(status_code=404, detail=f"Dataset {accession} not found")
        result = gse.to_dict()
        result["geo_url"] = (
            f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={accession.upper()}"
        )
        return result
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

SEARCH_GEO_DESCRIPTION = (
    "Find GEO series (GSE accessions) about a topic using hybrid semantic + lexical "
    "search with MeSH medical vocabulary expansion. "
    "`query` is what you are looking for in plain scientific words, e.g. "
    "\"single-cell RNA-seq of human pancreatic islets in type 2 diabetes\" or "
    "\"CRISPR screen in melanoma cell lines\". "
    "Returns accession, title, summary snippet, organism, assay type, submission date, "
    "sample count, PubMed ID, GEO URL, and relevance tier per result. "
    "Set top_k (default 20, max 200) to control result count. "
    "Set use_mesh=false to disable MeSH expansion for very specific queries. "
    "Set use_judge=false to skip the AI relevance judge (faster, no tier/reason)."
)

GET_DATASET_DESCRIPTION = (
    "Fetch full metadata for a single GEO dataset by accession number "
    "(e.g. GSE98386). Returns title, summary, organism, technology type, "
    "submission date, sample count, contributors, pubmed_id, supplementary files, "
    "and GEO URL."
)


@mcp_server.tool(name="search_geo", description=SEARCH_GEO_DESCRIPTION)
async def search_geo(
    query: str,
    ctx: Context,
    top_k: int = 20,
    use_mesh: bool = True,
    use_judge: bool = True,
) -> dict:
    """Search GEO datasets by topic."""
    if not query or not query.strip():
        raise ToolError("query must not be empty")
    if len(query) > 500:
        raise ToolError("query must be 500 characters or fewer")
    top_k = max(1, min(top_k, 200))

    try:
        results = await run_in_threadpool(_search, query.strip(), top_k, use_mesh, use_judge)
    except HTTPException as exc:
        raise ToolError(exc.detail)
    except Exception as exc:
        logger.exception("search_geo failed")
        raise ToolError(f"Search error: {exc}")

    return {
        "query": query,
        "result_count": len(results),
        "results": results,
    }


@mcp_server.tool(name="get_dataset", description=GET_DATASET_DESCRIPTION)
async def get_dataset(accession: str, ctx: Context) -> dict:
    """Fetch full metadata for a GEO dataset by accession."""
    accession = accession.strip().upper()
    if not accession.startswith("GSE"):
        raise ToolError("accession must be a GSE accession, e.g. GSE98386")

    try:
        return await run_in_threadpool(_fetch_dataset, accession)
    except HTTPException as exc:
        raise ToolError(exc.detail)
    except Exception as exc:
        logger.exception("get_dataset failed")
        raise ToolError(f"Fetch error: {exc}")


# ---------------------------------------------------------------------------
# FastAPI app with MCP mounted at /mcp
# ---------------------------------------------------------------------------

mcp_app = mcp_server.streamable_http_app(
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False
    ),
)


@asynccontextmanager
async def lifespan(_app):
    async with mcp_server.session_manager.run():
        yield


app = FastAPI(
    title="GEOSearch MCP Server",
    description="Model Context Protocol server for GEOSearch — AI-powered GEO dataset search.",
    version="1.0.0",
    lifespan=lifespan,
)

# A Route (not Mount) so /mcp is served exactly — no trailing-slash redirect
app.router.routes.append(Route("/mcp", mcp_app))


@app.get("/health")
def health():
    return {"status": "ok", "tools": ["search_geo", "get_dataset"]}
