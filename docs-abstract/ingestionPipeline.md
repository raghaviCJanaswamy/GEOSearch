# Ingestion Pipeline in GEOSearch

## The Core Idea

Ingestion is the process of fetching GEO dataset metadata from NCBI, parsing and normalising it, storing it in PostgreSQL, embedding it with PubMedBERT and storing the vector in Milvus, and auto-tagging it with MeSH terms — all in a single pipeline run.

---

## Two Entry Points

### 1. Query-based ingestion
Search NCBI GEO with a query (e.g., `"breast cancer RNA-seq[Strategy]"`) and ingest all matching datasets up to `retmax` records.

### 2. Accession-based ingestion
Provide a list of specific GSE accessions (e.g., `GSE123456`) and ingest exactly those datasets.

Both paths create an `IngestRun` record in the database to track status, counts, and errors.

---

## The Pipeline — Step by Step

```
NCBI ESearch / accession list
  │
  ├─ 1. Fetch summaries (ESummary API, batched 200 at a time)
  │       └─ Builds accession → summary map (avoids re-fetching per record)
  │
  ├─ 2. Filter existing (skip_existing=True by default)
  │
  ├─ 3. For each accession:
  │       ├─ Parse metadata (GEOParser)
  │       │     ├─ Normalise organisms (dict.fromkeys — preserves order)
  │       │     ├─ Infer tech_type from title/summary/design keywords
  │       │     ├─ Parse dates (handles 7 date formats)
  │       │     └─ Skip if title is empty (prevents blank NOT NULL records)
  │       └─ Store in PostgreSQL (db.merge — upsert)
  │
  ├─ 4. Batch embed all successful records (one provider call)
  │       ├─ prepare_embedding_text(): title×2 + summary + overall_design
  │       │     + Organism + Technology + MeSH tags (capped at 2000 chars)
  │       └─ Upsert all vectors to Milvus in one batch
  │
  └─ 5. Auto-tag with MeSH terms (MeSHMatcher)
          └─ Two-pass dictionary matching against all 30K MeSH terms
```

---

## Embedding Text Construction

Each dataset's embedding text is built from (`parser.py: prepare_embedding_text`):

```
title (repeated twice — extra weight for most signal-dense field)
+ summary
+ overall_design
+ "Organism: Homo sapiens"
+ "Technology: rna-seq"
+ "MeSH: Breast Neoplasms, RNA-Seq, ..."
```

**Capped at 2000 characters** — PubMedBERT truncates at 512 tokens internally, so text beyond ~2000 chars is silently ignored. The cap ensures the most important fields (title, summary) are always included rather than being pushed out by a very long `overall_design`.

MeSH tags in the embedding text are injected **after** MeSH tagging runs. Re-embedding after tagging improves semantic search quality — the dataset vector is anchored in the correct biomedical concept space.

---

## Technology Type Inference

The parser infers `tech_type` from keywords in `title + summary + overall_design`, in priority order:

| Tech type | Keywords matched |
|-----------|-----------------|
| single-cell | single-cell, scrna-seq, 10x genomics |
| rna-seq | rna-seq, rnaseq, transcriptome sequencing |
| chip-seq | chip-seq, chromatin immunoprecipitation |
| atac-seq | atac-seq, atacseq |
| methylation | methylation, bisulfite, wgbs, rrbs |
| wgs | whole genome sequencing |
| wes | whole exome sequencing |
| microarray | microarray, affymetrix, agilent |
| other-seq | sequencing, -seq |
| unknown | (no keyword matched) |

Single-cell is checked first because single-cell RNA-seq records also contain "rna-seq" keywords.

---

## Error Handling

| Failure | Effect | Recovery |
|---------|--------|----------|
| NCBI fetch error | `IngestItem.status = "failed"`, continue | Re-run pipeline |
| Empty title | Record skipped entirely | Check NCBI data quality |
| Parse failure | `IngestItem.status = "failed"`, continue | Re-run pipeline |
| Batch embedding failure | Records in DB but **missing from Milvus** | Run `reembed_all.py` |
| MeSH tagging failure | Non-fatal warning, tags skipped | Re-run MeSH tagger from UI |

Embedding errors are tracked separately (`embed_errors` counter) because they are non-fatal for database storage but critical for semantic search — records without Milvus vectors will not appear in semantic results.

---

## Rate Limiting

NCBI enforces rate limits on the E-utilities API:

| API key | Rate limit |
|---------|-----------|
| No key | 3 requests/second |
| With `NCBI_API_KEY` | 10 requests/second |

The `NCBIClient` automatically enforces these limits with `time.sleep(1 / rate_limit)` between requests and exponential backoff (up to 5 retries) on failures.

---

## Database Records Created

Each ingestion run creates:

| Table | Records |
|-------|---------|
| `ingest_run` | 1 per pipeline run |
| `ingest_item` | 1 per accession processed |
| `gse_series` | 1 per successfully parsed dataset (upsert) |
| `gse_mesh` | N per dataset (from auto-tagging) |

Milvus receives 1 vector per successfully embedded dataset.
