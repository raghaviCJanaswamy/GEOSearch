# Reciprocal Rank Fusion (RRF) in GEOSearch

## The Core Idea

Three independent retrieval systems each produce their own ranked list of datasets. RRF combines them into a single unified ranking without needing to normalise or compare their raw scores — only **rank positions** matter.

---

## The Three Input Lists

| List | Source | What it captures |
|------|--------|-----------------|
| **Semantic** | Milvus cosine similarity (PubMedBERT) | Meaning — finds datasets about the same concept even with different words |
| **Lexical** | PostgreSQL `ts_rank` full-text | Exact terminology — gene names, acronyms, clinical terms in text |
| **MeSH-only** | Direct `gse_mesh` tag lookup | Controlled-vocabulary tagging — datasets explicitly annotated with the query's disease/topic |

---

## The RRF Formula

For each dataset, RRF computes a contribution from each list it appears in:

```
contribution = weight / (k + rank)
```

Where:
- `rank` = position in that list (1 = top result)
- `k = 60` (smoothing constant — reduces the dominance of top-1 results)
- `weight` = importance assigned to that retrieval leg

The final RRF score sums contributions across all three lists:

```
RRF_score = (2.0 / (60 + semantic_rank))
           + (1.0 / (60 + lexical_rank))
           + (1.0 / (60 + mesh_rank))
```

A dataset appearing in only the semantic list at rank 1 scores `2.0/61 = 0.0328`.
A dataset appearing in all three lists at rank 10 each scores `2.0/70 + 1.0/70 + 1.0/70 = 0.0571` — higher, because multi-leg agreement is strong evidence of relevance.

---

## Why Semantic is Weighted 2×

The semantic leg receives double weight (`SEMANTIC_WEIGHT = 2.0`) while lexical and MeSH each receive `1.0`.

**Reason:** MeSH-only retrieval is extremely broad — a query for "breast cancer" matches every dataset tagged with `Breast Neoplasms`, regardless of whether it studies treatment options, genomics, imaging, or prognosis. Without the 2× semantic boost, these thousands of MeSH-tagged datasets would dominate the top results.

The semantic leg understands the *aspect* of the query — "treatment options for breast cancer" vs "breast cancer genomics" — and the 2× weight ensures this signal drives the final ranking.

---

## MeSH Boost (Post-RRF)

After RRF scoring, datasets directly tagged in `gse_mesh` with a MeSH ID matching the query receive an additional flat boost:

```
+0.1 per matching MeSH tag   (capped at +0.5)
```

This is applied on top of RRF, not as part of it. A vitiligo dataset carrying 3 vitiligo-related MeSH tags gets `+0.3` added to its RRF score, pulling it above untagged datasets with similar similarity scores.

**Combined score:**
```
final_score = RRF_score + MeSH_boost
```

---

## Why RRF Over Score Normalisation

The alternative — normalising all three scores to a common [0, 1] range and adding them — is fragile:
- Lexical `ts_rank` scores vary unpredictably by query length and field weights
- Cosine similarity scores vary by query type (common queries score 0.75–0.85; rare disease queries score 0.45–0.60)
- Normalising across these requires knowing the distribution in advance

RRF avoids this entirely by using only rank positions, which are always in the range [1, N] regardless of the underlying score distribution.

---

## Example — "pancreatic cancer RNA-seq"

| Dataset | Semantic rank | Lexical rank | MeSH rank | RRF score |
|---------|--------------|-------------|-----------|-----------|
| GSE71729 | 1 | 3 | 1 | 2/61 + 1/63 + 1/61 = 0.065 |
| GSE85217 | 2 | 1 | 5 | 2/62 + 1/61 + 1/65 = 0.064 |
| GSE93326 | 5 | — | 2 | 2/65 + 1/62 = 0.047 |
| GSE101000 | — | 2 | — | 1/62 = 0.016 |

GSE101000 — found only by lexical search — ranks well below datasets confirmed by multiple legs, correctly deprioritising a dataset that merely contains the words "pancreatic cancer" in passing.

---

## Configuration

| Parameter | Value | Location |
|-----------|-------|----------|
| `k` (smoothing constant) | 60 | `settings.rrf_k` / `RRF_K` in `.env` |
| Semantic weight | 2.0 | hardcoded `SEMANTIC_WEIGHT` in `hybrid_search.py` |
| Lexical weight | 1.0 | hardcoded `LEXICAL_WEIGHT` in `hybrid_search.py` |
| MeSH-only weight | 1.0 | hardcoded `MESH_WEIGHT` in `hybrid_search.py` |
| MeSH boost per tag | +0.1 (max +0.5) | hardcoded in `_get_mesh_boost_scores()` |
