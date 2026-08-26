# MeSH Auto-Tagging in GEOSearch

## The Core Idea

Every GEO dataset in the database is automatically tagged with relevant MeSH terms by scanning its title, summary, and overall_design for MeSH vocabulary. These tags serve two purposes: they power the MeSH-only retrieval leg of hybrid search, and they are injected into each dataset's embedding text to anchor its vector in biomedical concept space.

---

## How Tagging Works — Two-Pass Matching

`MeSHMatcher` loads all MeSH terms into memory once per process (class-level cache), then scans each dataset's text in two passes:

### Pass 1 — Single-word token lookup (O(tokens) dict hits)
Every word ≥4 chars in the dataset text is looked up in a pre-built `single_lookup` index:
```
single_lookup: word → [(mesh_id, base_confidence)]
```
Only single-word MeSH terms with ≥5 chars are indexed here. Base confidence is `0.4` — lower than phrase matches because single words are less specific.

### Pass 2 — Multi-word phrase scan (candidate filtering)
The system checks only multi-word MeSH terms whose **first word** appears in the dataset text — avoiding scanning all 30K phrases against every dataset:
```
phrase_by_first_word: first_word → {phrase → [mesh_id]}
```
For each candidate phrase found in the text, confidence is calculated by phrase length:
```
confidence = min(1.0, 0.5 + word_count × 0.15) × field_weight
```
A 3-word phrase like "Breast Neoplasms Male" scores `0.5 + 3×0.15 = 0.95` (before field weight).

---

## Field Weights

Text fields are weighted differently to reflect their signal quality:

| Field | Weight | Rationale |
|-------|--------|-----------|
| `title` | 2.0 | Most curated, highest signal |
| `summary` | 1.5 | Broad context |
| `overall_design` | 1.0 | Methodology detail |

A MeSH term found in the title scores twice as high as the same term found only in the overall_design.

---

## Confidence Scoring

Each dataset–MeSH association gets a confidence score in [0, 1]. For datasets matched by multiple fields, the **maximum** score across fields is taken (not sum) to avoid inflating scores for terms that repeat across fields.

**Confidence threshold:** Only associations with `confidence ≥ 0.3` (configurable via `MESH_CONFIDENCE_THRESHOLD` in `.env`) are written to `gse_mesh`.

---

## What Gets Stored

```sql
gse_mesh(
  accession   TEXT,      -- e.g. GSE123456
  mesh_id     TEXT,      -- e.g. D001943
  source      ENUM,      -- 'auto' (tagger) | 'pubmed' | 'manual'
  confidence  FLOAT      -- 0.0 – 1.0
)
```

Associations have a unique constraint on `(accession, mesh_id, source)` — the tagger uses `db.merge()` (upsert) so re-running it is safe and idempotent.

---

## How Tags Feed Into Search

### MeSH-only retrieval leg
`_mesh_only_search()` queries `gse_mesh` directly for all datasets tagged with any MeSH ID matched by the query expander. For single-concept queries, all tagged datasets are returned (union). For multi-concept queries, intersection logic requires tags from each concept group.

### MeSH boost
After RRF fusion, `_get_mesh_boost_scores()` adds `+0.1` per matching MeSH tag (max `+0.5`) to each dataset's score. A dataset tagged with 4 relevant MeSH terms gets `+0.4` on top of its RRF score.

### Embedding enrichment
`prepare_embedding_text()` injects MeSH tags into the text used to generate each dataset's Milvus vector:
```
"MeSH: Breast Neoplasms, RNA-Seq, Tumor Microenvironment"
```
This anchors the embedding vector directly in the clinical concept space of PubMedBERT, improving recall for disease queries even when the disease name doesn't appear verbatim in the title or summary.

---

## When to Re-run

| Situation | Action |
|-----------|--------|
| New datasets ingested | Auto-runs at end of ingestion pipeline |
| MeSH dictionary updated | Re-run tagger from UI → Data Ingestion → MeSH Tagger |
| Confidence threshold changed | Re-run with `overwrite=True` to replace existing tags |
| After backfill populates `overall_design` | Re-run tagger — new text field adds more matches |
