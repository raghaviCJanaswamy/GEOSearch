# Lexical Search in GEOSearch

## The Core Idea

Lexical search finds datasets where the words in the query appear in the dataset's text fields. Unlike semantic search (which matches meaning), lexical search is exact and fast — it catches cases where the user's exact terminology is present in the title or summary, and is critical for acronyms, gene names, and assay types that embeddings may not capture well.

---

## How It Works — PostgreSQL Full-Text Search

GEOSearch uses PostgreSQL's built-in `tsvector` / `tsquery` full-text search engine. Each query is matched against a **weighted composite document** built from three fields:

| Field | Weight | Rationale |
|-------|--------|-----------|
| `title` | A (highest) | Concise, author-chosen descriptor — most signal-dense |
| `summary` | B | Submitter description — broad context |
| `overall_design` | C | Experimental design — methodological detail |

The composite is built as:
```sql
setweight(to_tsvector('english', title), 'A') ||
setweight(to_tsvector('english', summary), 'B') ||
setweight(to_tsvector('english', overall_design), 'C')
```

PostgreSQL's `ts_rank` scores each dataset by how many query terms appear and in which weight tier — title matches score highest.

---

## Query Construction — Three Layers

### Layer 1 — Phrase match (original query)
The raw query is fed to `plainto_tsquery('english', query)`, which handles stemming automatically (`cancer` matches `cancers`, `cancerous`).

### Layer 2 — Prefix AND match (catches stemming variants)
Each query word gets a prefix-match version: `pancreatic` → `pancrea:*`, which matches both `pancreas` and `pancreatic`. Words are AND'd together so all must appear:
```sql
pancrea:* & cancer:*
```
This catches records that say "pancreas cancer" when the user typed "pancreatic cancer".

### Layer 3 — Cancer domain synonym expansion
If the query contains any cancer term (`cancer`, `tumor`, `neoplasm`, `carcinoma`, `malignancy`, `adenocarcinoma`), the system automatically OR-expands with oncology vocabulary:
```sql
(pancrea:*) & (cancer:* | tumor:* | neoplas:* | carcinoma:* | adenocarcinoma:* | malign:*)
```
This catches records using alternative clinical terminology for the same disease.

### Layer 4 — MeSH preferred names (OR'd in)
The top 5 MeSH preferred names matched during query expansion are OR'd in as additional match pathways:
```sql
... || plainto_tsquery('english', 'Pancreatic Neoplasms')
    || plainto_tsquery('english', 'Carcinoma, Pancreatic Ductal')
```
This is critical for lay-term queries like "heart attack" — the original words may not appear in clinical papers, but the MeSH term "Myocardial Infarction" will.

For long MeSH terms (first word ≥8 chars), a prefix variant is also OR'd in:
```sql
|| to_tsquery('english', 'myocard:*')   -- catches myocardial AND myocardium
```

**MeSH expansion is capped at 5 terms** to prevent over-retrieval — without the cap, "lung cancer" would OR-expand to dozens of cancer subtype names and match thousands of loosely related datasets.

---

## Multi-Concept AND Logic

For queries with multiple distinct concepts (e.g., "breast cancer organ transplant"), the system detects the concept groups from MeSH expansion and requires the dataset text to match tokens from **each group**:

```python
# Each concept group must match
for group_ids in mesh_concept_groups:
    group_names = [mesh_id_to_name[mid] for mid in group_ids]
    # Build OR tsquery for this group, then AND across all groups
```

This mirrors NCBI's AND behaviour — a dataset about breast cancer but not organ transplant is excluded from a two-concept query.

---

## What Lexical Search Catches That Semantic Misses

| Case | Example | Why lexical wins |
|------|---------|-----------------|
| Gene symbols | `BRCA1`, `TP53` | Embeddings don't distinguish gene names well |
| Assay acronyms | `ATAC-seq`, `ChIP-seq`, `WGBS` | Rare tokens; embeddings generalise over them |
| Dataset IDs | `GSE12345` | Not in embedding vocabulary |
| Exact disease names | `glioblastoma multiforme` | Present verbatim in title |
| Recent terminology | `COVID-19`, `SARS-CoV-2` | May be underrepresented in pre-trained model |

---

## Where Results Go

Lexical results (accession + `ts_rank` score) are passed to RRF fusion alongside semantic and MeSH-only results. The `ts_rank` score is not used directly — only the **rank position** matters for RRF, where each result contributes `1 / (60 + rank)` to the final score.
