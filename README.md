# GEOSearch: AI-Powered Semantic Search for NCBI GEO

GEOSearch is an open-source hybrid search engine for [NCBI GEO](https://www.ncbi.nlm.nih.gov/geo/) dataset discovery. It combines dense semantic embeddings, PostgreSQL full-text search, and MeSH ontology expansion — fused by Reciprocal Rank Fusion — with an optional LLM relevance judge and on-demand expression matrix retrieval from ARCHS4.

**Live deployment:** http://149.165.172.79:8501
**API docs:** http://149.165.172.79:8080/docs
**MCP endpoint:** http://149.165.172.79:8090/mcp

---

## Features

- **Semantic search** — Azure OpenAI `text-embedding-3-large` (3072-dim) over 263,000+ GSE records in Milvus IVF_FLAT
- **Lexical search** — PostgreSQL `ts_rank_cd` with weighted tsvector (title > summary > design)
- **MeSH expansion** — MeSH 2024 synonym expansion with word-boundary-safe matching (30,764 terms)
- **Hybrid RRF fusion** — Weighted Reciprocal Rank Fusion (semantic ×2.0, lexical ×1.5, k=60)
- **LLM relevance judge** — Post-retrieval tier assignment (Best / Possible / Low) with one-sentence rationale
- **Expression matrix API** — On-demand retrieval from ARCHS4 H5 (67,186 genes × 1,098,771 samples), no intermediate download
- **REST API** — FastAPI with JSON, streaming TSV, and multipart response modes
- **MCP interface** — Model Context Protocol tool for AI agent integration
- **Multi-species corpus** — Human, mouse, rat, Drosophila, zebrafish, Arabidopsis, yeast, C. elegans

---

## Architecture

```
User Query
    │
    ├─ MeSH Expansion ──► expanded_query
    │
    ├─ Semantic  (Milvus IVF_FLAT, top-1000, cosine ≥ 0.45) ──┐
    ├─ Lexical   (PostgreSQL FTS, top-100)                     ─┼─► RRF Fusion ──► LLM Judge ──► Results
    └─ (MeSH tokens appended to both paths)                    ─┘

Results ──► Streamlit UI
        ──► FastAPI REST API  (/search, /ask, /expression/{accession})
        ──► MCP Tool          (search_geo, get_dataset)
        ──► ARCHS4 H5         (expression matrix on demand)
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| UI | Streamlit |
| API | FastAPI + Uvicorn |
| Metadata DB | PostgreSQL 15 |
| Vector DB | Milvus 2.3 (IVF_FLAT, nlist=1024) |
| Embeddings | Azure OpenAI `text-embedding-3-large` (3072-dim) |
| LLM Judge | Azure OpenAI `gpt-6-luna` |
| Expression data | ARCHS4 v2 H5 (`human_gene_v2.latest.h5`) via h5py |
| ORM | SQLAlchemy 2 |
| Config | pydantic-settings |
| Deployment | Docker Compose (arm64 local / amd64 VM) |

---

## Corpus

| Species | Records |
|---|---|
| Homo sapiens | 135,159 |
| Mus musculus | ~96,000 |
| Drosophila melanogaster | ~6,900 |
| Arabidopsis thaliana | ~6,800 |
| Rattus norvegicus | ~6,200 |
| Caenorhabditis elegans | ~4,700 |
| Saccharomyces cerevisiae | ~4,400 |
| Danio rerio | ~3,000 |
| **Total** | **263,155** |

---

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Azure OpenAI API key and endpoint (for embeddings and judge)
- ARCHS4 H5 file (`human_gene_v2.latest.h5`) — obtain from [maayanlab.cloud/archs4](https://maayanlab.cloud/archs4/)
- NCBI email (for E-utilities ingestion)

### 1. Clone and configure

```bash
git clone <repo-url>
cd GEOSearch
cp .env.example .env
# Edit .env — set AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, NCBI_EMAIL
```

### 2. Place ARCHS4 H5 file

```bash
# Place the file at the repo root (excluded from Docker build context via .dockerignore)
ls human_gene_v2.latest.h5
```

### 3. Start the stack

```bash
docker compose up -d
```

Services start in order: PostgreSQL → Milvus → init → app + API.

| Service | URL |
|---|---|
| Streamlit UI | http://localhost:8501 |
| FastAPI | http://localhost:8080 |
| API Docs | http://localhost:8080/docs |
| MCP | http://localhost:8090/mcp |

---

## Environment Variables

Key settings in `.env` (see `config.py` for full list):

```bash
# Azure OpenAI (required)
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_ENDPOINT=https://....openai.azure.com/
AZURE_OPENAI_API_VERSION=2024-02-01
AZURE_OPENAI_DEPLOYMENT=text-embedding-3-large

# Embeddings
EMBEDDING_PROVIDER=azure_openai
EMBEDDING_MODEL=text-embedding-3-large
EMBEDDING_DIMENSION=3072

# NCBI
NCBI_EMAIL=your.email@example.com

# Search tuning
SEMANTIC_TOP_K=1000
LEXICAL_TOP_K=100
FINAL_TOP_K=750
RRF_K=60

# Judge
JUDGE_ENABLED=true
JUDGE_TOP_K=150
REWRITE_ENABLED=false
```

---

## API Reference

### Search

```bash
# JSON results
curl "http://localhost:8080/search?query=breast+cancer+RNA-seq&top_k=20"

# With judge disabled
curl "http://localhost:8080/search?query=MASLD+liver&use_judge=false"

# Streaming TSV of expression matrices
curl "http://localhost:8080/search?query=hepatocellular+carcinoma&format=tsv&expression_genes=TP53,MYC"
```

### Expression Matrix

```bash
# Expression info for a GSE
curl "http://localhost:8080/expression/GSE123456"

# Download expression matrix as TSV (specific genes)
curl "http://localhost:8080/expression/GSE123456/download?genes=TP53,BRCA1,MYC"
```

### MCP (AI Agents)

Add to Claude Desktop `config.json`:

```json
{
  "mcpServers": {
    "geosearch": { "type": "http", "url": "http://149.165.172.79:8090/mcp" }
  }
}
```

Tools available: `search_geo`, `get_dataset`

---

## Project Structure

```
GEOSearch/
├── app.py                    # Streamlit UI
├── api.py                    # FastAPI REST API
├── mcp_server.py             # MCP tool server
├── config.py                 # Settings (pydantic-settings)
├── db/
│   ├── models.py             # GSESeries SQLAlchemy model
│   └── __init__.py
├── expression/
│   └── archs4.py             # ARCHS4 H5 accessor + singleton
├── search/
│   ├── hybrid_search.py      # RRF pipeline
│   ├── judge.py              # LLM relevance judge
│   ├── rewrite.py            # LLM query rewriter (disabled)
│   └── llm.py                # Azure OpenAI helper
├── mesh/
│   └── query_expand.py       # MeSH expansion
├── vector/
│   ├── embeddings.py         # Embedding providers
│   └── milvus_store.py       # Milvus operations
├── geo_ingest/
│   └── ingest_pipeline.py    # GEO metadata ingestion
├── scripts/
│   ├── init_database.py      # Schema init
│   ├── gap_fill_ingest.py    # Gap-fill ingestion (resumable)
│   └── batch_search.py       # Benchmark runner
├── benchmarks-detailed/      # Benchmark ground truth + results
├── docs/                     # Manuscript, project status
├── docker-compose.yml        # Local (arm64) stack
├── docker-compose.linux.yml  # Remote VM (amd64) stack
└── requirements.txt
```

---

## License

MIT — see LICENSE file.

## Citation

```bibtex
@software{geosearch2026,
  title  = {GEOSearch: AI-Powered Semantic Search for NCBI GEO},
  author = {Ge, Xijin and Janaswamy, Raghavi},
  year   = {2026},
  url    = {https://github.com/sdsu-comp-bio/GEOSearch}
}
```

## Acknowledgements

NCBI GEO and ARCHS4 teams for open-access genomics data infrastructure.
