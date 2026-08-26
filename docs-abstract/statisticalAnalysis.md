# Statistical Analysis: GEOSearch vs. NCBI GEO Native Search

## What We Are Proving

NCBI GEO's native search is keyword-only with no semantic understanding and no intelligent MeSH expansion. Users must know exact MeSH terms, exact accession formats, and Boolean syntax. GEOSearch replaces that with a hybrid pipeline (PubMedBERT semantic + PostgreSQL lexical + MeSH-tag retrieval + RRF fusion).

The statistical question is: **does GEOSearch return more relevant results, ranked better, than NCBI GEO native search — especially for lay-term queries, rare diseases, and multi-concept queries?**

---

## NCBI GEO Native Search — Specific Limitations

| Limitation | Effect |
|-----------|--------|
| Keyword-only matching | "heart attack" does not find "myocardial infarction" datasets |
| No semantic understanding | "childhood leukaemia" and "paediatric ALL" are unrelated to the system |
| Manual MeSH tagging required | User must know and type exact MeSH syntax: `myocardial infarction[MeSH Terms]` |
| Single-field OR across all fields | No field weighting — a MeSH match in a comment is equal to a title match |
| No rank fusion | Results sorted by relevance score without combining multiple signals |
| No lay-term expansion | "blood cancer" returns nothing useful without knowing it maps to leukemia/lymphoma |
| No adaptive threshold | Either a result matches or it does not — no fallback for rare diseases |

---

## Evaluation Framework — What to Measure

### Primary Metrics

**1. Precision@k (P@k)**
Fraction of the top-k returned results that are relevant.
```
P@k = |{relevant docs in top k}| / k
```
Measure at k = 5 and k = 10. P@10 is the standard IR reporting metric.

**2. Mean Reciprocal Rank (MRR)**
Rewards systems that put the first relevant result higher up.
```
MRR = (1/|Q|) × Σ  1 / rank_of_first_relevant_result
```
MRR = 1.0 means every query's first result is relevant. MRR = 0.5 means the first relevant result is on average at rank 2.

**3. Recall@k**
Fraction of all relevant documents in the corpus that appear in the top-k results.
```
Recall@k = |{relevant docs in top k}| / |total relevant docs|
```
Critical for rare diseases — a system that returns 0 results has Recall = 0.

**4. NDCG@10 (Normalised Discounted Cumulative Gain)**
Unlike P@k, rewards graded relevance (highly relevant > somewhat relevant > not relevant) and penalises systems that bury relevant results lower in the list.
```
NDCG@k = DCG@k / IDCG@k

DCG@k = Σ  rel_i / log2(i + 1)    (rel_i = relevance grade of result at position i)
```

---

## Query Set Design — 50 Queries Across 5 Domains

The 50-query benchmark already implemented in `streamlit_analytics.py` (`IR_EVAL_QUERIES`) covers:

| Domain | Count | Examples |
|--------|-------|---------|
| Oncology | 10 | breast cancer RNA-seq, pancreatic cancer transcriptome |
| Neurological | 8 | Alzheimer's disease brain, ALS motor neuron disease |
| Metabolic / Cardiovascular | 8 | type 2 diabetes insulin resistance, myocardial infarction heart attack |
| Infectious / Immune | 8 | COVID-19 SARS-CoV-2, lupus autoimmune SLE |
| Rare / Niche | 8 | vitiligo melanocyte, Niemann-Pick sphingolipid |
| Technology | 8 | single-cell RNA-seq, ATAC-seq chromatin accessibility |

**Critical design principle:** The query set deliberately mixes:
- **Common broad queries** (breast cancer) — where NCBI also performs well
- **Lay-term queries** (heart attack, blood cancer) — where NCBI fails, GEOSearch wins via MeSH aliases
- **Rare disease queries** (vitiligo, Gaucher disease) — where NCBI returns sparse results, GEOSearch uses adaptive threshold cascade
- **Multi-concept queries** (breast cancer RNA-seq) — where GEOSearch's semantic leg understands the conjunction

---

## Relevance Labelling Strategy

### Approach 1 — MeSH-Tag Proxy (already implemented)
A result is **relevant** if it is tagged in `gse_mesh` with at least one MeSH preferred name from the query's `relevant_mesh` list. This enables automated, scalable evaluation without manual annotation.

**Limitation:** Circular — GEOSearch's MeSH tagger created the tags used to judge relevance. NCBI results evaluated against this standard have an inherent disadvantage because NCBI's tags come from a different source (PubMed linkage vs. text mining).

### Approach 2 — Expert Annotation (gold standard)
For each query, a domain expert manually labels the top-20 results from both GEOSearch and NCBI as:
- **2** = Highly relevant (correct disease/assay, correct organism)
- **1** = Partially relevant (related topic, different aspect)
- **0** = Not relevant

Expert annotation eliminates the circularity problem and enables NDCG computation. Minimum viable annotation: 20 queries × top-20 results × 2 systems = 800 judgements (~4–6 hours for a biologist).

### Approach 3 — NCBI GEO Accession Ground Truth
For precision queries (e.g., "GSE datasets about vitiligo in Homo sapiens"), the true positive set can be established by manually reviewing NCBI GEO's filtered results as the gold standard, then checking which appear in GEOSearch's top-k.

**Recommended:** Use Approach 1 for automated large-scale benchmarking, Approach 2 for a 20-query publication-quality validation subset.

---

## Statistical Tests

### 1. Wilcoxon Signed-Rank Test (primary)
**Use for:** Comparing P@10 or MRR scores between GEOSearch and NCBI across all 50 queries.

**Why Wilcoxon, not paired t-test:**
- P@10 scores are bounded [0, 1] and skewed — not normally distributed
- Wilcoxon is non-parametric and robust to outliers
- Paired design (same query evaluated on both systems) controls for query difficulty

```
H₀: median(P@10_GEOSearch - P@10_NCBI) = 0
H₁: median(P@10_GEOSearch - P@10_NCBI) > 0  (one-tailed)

Reject H₀ if p < 0.05
```

**Expected result:** GEOSearch should show significantly higher P@10 on lay-term and rare-disease query subsets. Common queries may be comparable.

### 2. McNemar's Test (binary relevance)
**Use for:** Comparing whether each system returns at least one relevant result in the top-10 (binary outcome per query).

```
H₀: Probability of GEOSearch finding a relevant result = Probability of NCBI finding one
H₁: GEOSearch finds relevant results more often (especially rare diseases)
```

Particularly powerful for the rare disease subset — NCBI will return 0 relevant results for many lay-term queries while GEOSearch finds them via MeSH alias expansion.

### 3. Effect Size — Cohen's d
Report effect size alongside p-values so the magnitude of improvement is clear, not just statistical significance.

```
d = (mean_GEOSearch - mean_NCBI) / pooled_std

d ≈ 0.2  small effect
d ≈ 0.5  medium effect
d ≈ 0.8  large effect
```

### 4. Subgroup Analysis (most compelling)
Run all metrics separately for each query domain:

| Domain | Expected GEOSearch advantage |
|--------|----------------------------|
| Lay-term queries | Large — NCBI cannot map "heart attack" → MeSH |
| Rare diseases | Large — adaptive threshold cascade vs. NCBI returning 0 |
| Common broad queries | Small or none — both systems find obvious results |
| Technology queries | Medium — semantic leg understands assay relationships |

Demonstrating that GEOSearch's advantage is **largest exactly where NCBI's limitations are most severe** (lay terms, rare diseases) is the strongest possible statistical argument.

---

## Ablation Study — Isolating the Value of Each Component

Run the 50-query benchmark across 4 configurations and report P@10 + MRR for each:

| Configuration | Semantic | Lexical | MeSH | What it proves |
|--------------|----------|---------|------|---------------|
| Lexical only | ✗ | ✓ | ✗ | Closest analogue to NCBI keyword search |
| Semantic only | ✓ | ✗ | ✗ | Value of PubMedBERT alone |
| Hybrid (no MeSH) | ✓ | ✓ | ✗ | Value of semantic + lexical over keyword-only |
| Full hybrid | ✓ | ✓ | ✓ | Full system — all three legs active |

**The critical comparison:** `Lexical only` vs `Full hybrid`. This directly quantifies how much better GEOSearch is than NCBI-equivalent search.

This ablation is already implemented in `streamlit_analytics.py` (`_section_formal_ablation()`). Run it and record the output.

---

## Recall Analysis — The Zero-Result Problem

NCBI GEO returns **zero results** for many valid queries because users do not know MeSH syntax. Quantify this:

1. Submit all 50 queries as plain-text to NCBI GEO (no MeSH qualifiers)
2. Record how many return 0 results or < 5 results
3. Run the same queries through GEOSearch
4. Report: **GEOSearch returns non-zero results for X% more queries than NCBI plain-text search**

For rare disease lay-term queries (e.g., "blood cancer", "throat cancer", "high blood pressure"), NCBI will consistently return poor results without explicit MeSH syntax. GEOSearch's `LAY_TERM_ALIASES` and adaptive threshold cascade are specifically designed to handle this.

---

## Latency Analysis

Statistical performance is not just about relevance — it also includes query speed. Measure and report:

| Metric | Target |
|--------|--------|
| Mean query latency | < 2 seconds end-to-end |
| P95 latency | < 5 seconds |
| Semantic search latency | ~300–800ms (Milvus FLAT index, 129K vectors) |
| Lexical search latency | ~50–200ms (PostgreSQL tsvector) |
| MeSH expansion latency | ~100–400ms (5-pass DB query) |

NCBI's API enforces a 3 req/sec rate limit without an API key — GEOSearch serves results from local infrastructure with no such constraint.

---

## Expected Results Summary

Based on the system design and the retrieval architecture:

| Query type | NCBI P@10 (expected) | GEOSearch P@10 (expected) | Primary driver |
|-----------|---------------------|--------------------------|---------------|
| Common broad | 0.70–0.85 | 0.75–0.90 | Both strong; semantic adds nuance |
| Lay-term | 0.10–0.30 | 0.60–0.80 | MeSH alias expansion |
| Rare disease | 0.00–0.20 | 0.40–0.65 | Adaptive threshold + MeSH tagger |
| Multi-concept | 0.40–0.60 | 0.65–0.85 | Semantic leg understands conjunction |
| Technology terms | 0.50–0.70 | 0.70–0.85 | PubMedBERT assay vocabulary |

**Overall expected: GEOSearch P@10 ≈ 0.70–0.80, NCBI P@10 ≈ 0.40–0.55 on the same 50-query set**

---

## What to Write Up

A publication-quality evaluation section should contain:

1. **Query set description** — 50 queries, 5 domains, rationale for mix
2. **Relevance labelling** — method (MeSH-proxy or expert annotation), inter-annotator agreement if manual
3. **Ablation table** — 4 configurations × P@10 + MRR
4. **GEOSearch vs. NCBI comparison table** — P@10, MRR, Recall@10 per domain
5. **Statistical test results** — Wilcoxon signed-rank p-value, effect size (Cohen's d)
6. **Zero-result analysis** — % of queries where NCBI returns < 5 results vs. GEOSearch
7. **Latency table** — mean and P95 per pipeline stage

This framing positions GEOSearch as solving a **concrete, measurable problem** in biomedical dataset discovery — not just an alternative to NCBI, but a complement that handles the cases NCBI cannot.
