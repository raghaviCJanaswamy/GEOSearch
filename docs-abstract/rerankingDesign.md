# Re-ranking Design: Evaluation of LLM-Based Approaches

## Proposed Approach Under Review

Two ideas were evaluated as potential improvements to GEOSearch result quality:

1. A CSV dump of GSE IDs and descriptions from NCBI, used as a search input source
2. Feeding GEOSearch results to ChatGPT to re-rank by relevance based on descriptions

---

## Part 1 — CSV Dump of GSE IDs + Descriptions from NCBI

**This is already what GEOSearch has.** The ingestion pipeline fetches exactly this data from NCBI — title, summary, overall_design, organism, technology type — and stores it in PostgreSQL + Milvus. A CSV dump adds no new information; it is a different format of the same source data.

**What it does not fix:** NCBI's CSV still has the same keyword-only indexing problem. Starting from NCBI's ranked list means inheriting all the retrieval quality issues documented in [statisticalAnalysis.md](statisticalAnalysis.md):

- No semantic understanding — "heart attack" does not find "myocardial infarction" datasets
- No lay-term expansion — "blood cancer" returns nothing without MeSH syntax
- No adaptive threshold — rare diseases return zero results
- No rank fusion — a MeSH match in a comment is equal to a title match

A CSV of NCBI results is a retrieval starting point, not a search improvement.

---

## Part 2 — Feeding GEOSearch Results to ChatGPT for Re-ranking

This is **LLM-based re-ranking** — a recognised technique in information retrieval, but with specific tradeoffs that make it a poor structural fit for GEOSearch.

| Aspect | Reality |
|--------|---------|
| Does it improve ranking? | Sometimes — LLMs can reason about relevance better than RRF scores for nuanced queries |
| Cost | API call per search query; latency adds 1–3 seconds per request |
| Context window limit | You can only pass ~20–50 results before hitting token limits or cost ceiling |
| Hallucination risk | ChatGPT may confidently say a dataset is relevant when it is not — it does not access the actual data |
| Circular problem | If GEOSearch already retrieves the right top-20, re-ranking helps marginally. If retrieval misses a dataset, ChatGPT cannot surface it |

### The Core Issue

Re-ranking only rearranges what retrieval already found. If a relevant dataset is not in the top-500 returned by Milvus, ChatGPT never sees it and cannot correct the gap.

The retrieval stage — semantic search via PubMedBERT + lexical search + MeSH-tag retrieval — determines which datasets are candidates. Re-ranking operates on that fixed candidate set. A re-ranker cannot compensate for a retrieval gap.

---

## Better Alternatives

### 1. Cross-Encoder Re-ranking (preferred if re-ranking is needed)

Use a biomedical cross-encoder such as `cross-encoder/ms-marco-MiniLM` to re-score the top-50 RRF results. A cross-encoder reads the query and each document together (unlike a bi-encoder which embeds them separately), giving finer relevance judgements.

**Advantages over ChatGPT re-ranking:**
- No API cost — runs locally
- Deterministic output — same query always produces same ranking
- Sub-second latency on CPU for 50 documents
- Standard IR pipeline: bi-encoder retrieval → cross-encoder re-rank

**Limitation:** Same candidate-set constraint — only re-orders what RRF already returned.

### 2. Query Understanding Before Search (higher impact)

Use an LLM *before* the search to expand the user's query into structured fields:

```
User query: "blood cancer in children treated with immunotherapy"

LLM output:
  disease: leukemia, lymphoma
  organism: Homo sapiens
  age_group: pediatric
  treatment: immunotherapy
  assay: (any)
```

These structured fields are then passed into GEOSearch — disease terms fed into MeSH expansion, organism as a filter, assay type into the lexical leg. This improves **retrieval** rather than just re-ranking, and does not require a round-trip on every query if the structured fields can be cached.

### 3. What Already Exists (and directly addresses the problem)

GEOSearch's MeSH expansion already performs the core function that "smart search" needs: semantic mapping of lay terms to clinical concepts. `LAY_TERM_ALIASES` maps "blood cancer" → leukemia/lymphoma, "heart attack" → myocardial infarction, etc. The adaptive threshold cascade ensures rare diseases are not dropped.

The gap, if any, is **coverage** — which lay terms are in the alias table, which MeSH descriptors are loaded — not re-ranking logic.

---

## Recommendation

| Approach | Impact | Cost | Complexity |
|----------|--------|------|------------|
| CSV dump from NCBI | None — duplicates existing data | Low | Low |
| ChatGPT re-ranking | Low-to-medium — cannot fix retrieval gaps | High (API cost + latency) | Medium |
| Cross-encoder re-ranking | Medium — improves top-k ordering | Low (local) | Medium |
| LLM query understanding (pre-search) | High — improves retrieval, not just ordering | Medium (one LLM call per novel query) | Medium-High |
| Expand MeSH alias coverage | High — directly fixes lay-term retrieval gap | Low | Low |

**Bottom line:** Feeding results to ChatGPT for re-ranking is a reasonable experiment but not a structural improvement — it optimises the last step while leaving the harder retrieval problem unchanged. Query understanding *before* search, or expanding the existing MeSH alias table, would be more impactful.
