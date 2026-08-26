# MeSH Query Expansion in GEOSearch

## The Core Idea

Users search in lay language — "heart attack", "blood cancer", "high blood pressure". GEO datasets are described in clinical language — "Myocardial Infarction", "Hematologic Neoplasms", "Hypertension". MeSH query expansion bridges this gap by automatically mapping user terms to their controlled-vocabulary equivalents before search begins.

---

## What is MeSH?

**Medical Subject Headings (MeSH)** is the NLM's controlled biomedical vocabulary — 30,000+ terms used to index PubMed articles and GEO datasets. Each MeSH term has:

- A **preferred name** (e.g., `Myocardial Infarction`)
- **Entry terms / synonyms** (e.g., `heart attack`, `cardiac infarction`, `MI`)
- A **MeSH ID** (e.g., `D009203`)
- A **tree number** indicating its position in the hierarchy

GEOSearch loads all MeSH terms into PostgreSQL at startup and uses them for both query expansion and dataset tagging.

---

## The Five-Pass Matching Pipeline

When a query arrives, the expander tokenises it into unigrams, bigrams, and trigrams, then runs five passes against the MeSH database in priority order:

| Pass | Match type | Token requirements |
|------|-----------|-------------------|
| 1 | Exact preferred name | Multi-word OR ≥8 chars |
| 2 | Exact entry term (synonym) | ≥6 chars |
| 3 | Preferred name starts with token | Multi-word OR ≥8 chars |
| 4 | Preferred name contains token | Multi-word only |
| 5 | Entry term contains token (lay-term fallback) | Multi-word only, ≥6 chars |

Matches found in earlier passes have higher priority. The expander stops after collecting `max_terms` (default: 5) unique MeSH terms.

---

## What Gets Blocked

### Overly broad MeSH IDs

Terms that match almost every biomedical dataset are excluded entirely:

| MeSH ID | Term | Why blocked |
|---------|------|------------|
| D009369 | Neoplasms | Matches all cancer datasets |
| D006801 | Humans | Matches nearly everything |
| D051379 | Mice | Too generic |
| D008297 | Male | Demographic, not disease |
| D005260 | Female | Demographic, not disease |

### Generic modifier words

Single words like `treatment`, `therapy`, `gene`, `cell`, `protein` are blocked from driving MeSH lookup on their own. The word "treatment" appears as a prefix in hundreds of MeSH descriptors (Treatment Failure, Treatment Outcome, Therapeutics…) and would produce massively over-broad results as a standalone token. These words are only meaningful as part of multi-word phrases caught by the bigram/trigram tokenisation.

### Comma-qualified variants

MeSH contains terms like `Breast Neoplasms, Male` — a more specific variant with a comma qualifier. These are excluded unless the qualifier word (here, `male`) actually appears in the user's query. This prevents "breast cancer" from matching `Breast Neoplasms, Male` and retrieving only male-specific datasets.

---

## Lay Term Aliases

Some common phrases have no direct MeSH entry term. These are handled via a hardcoded lookup table before MeSH matching runs:

| Lay term | Maps to |
|----------|---------|
| `heart attack` | myocardial infarction, cardiac infarction |
| `high blood pressure` | hypertension |
| `blood cancer` | leukemia, lymphoma, multiple myeloma, hematologic neoplasms |
| `throat cancer` | head and neck neoplasms, oropharyngeal neoplasms, laryngeal neoplasms |
| `throat` | pharynx, oropharynx, larynx, nasopharynx |

---

## What Expansion Produces

For each matched MeSH term, expansion contributes:

1. **Preferred name** → added to the lexical search as an OR clause (if not already in the query)
2. **Up to 2 entry terms** → added to the expanded query text fed into semantic embedding
3. **MeSH ID** → used to boost datasets tagged with this term in `gse_mesh`
4. **Source token** → tracked to group terms by concept for multi-concept AND logic

### Example — `"breast cancer RNA-seq"`

| Matched MeSH | Preferred name | Sample entry terms added |
|---|---|---|
| D001943 | Breast Neoplasms | mammary cancer, breast tumor |
| D012333 | RNA | ribonucleic acid |

Expanded query sent to semantic search:
```
"breast cancer RNA-seq Breast Neoplasms mammary cancer ribonucleic acid"
```

---

## Multi-Concept AND Logic

When a query contains multiple distinct concepts (e.g., "breast cancer organ transplant"), each concept produces its own MeSH group. For lexical and MeSH-only search, a dataset must match at least one term from **each concept group** — not just any one term. This mirrors NCBI's AND behaviour and prevents single-concept datasets (6,000+ breast cancer datasets) from flooding results for a two-concept query.

---

## Where This Feeds

```
Query text
  └─ QueryExpander.expand_query()
        ├─ expanded_query  → semantic_search() (blended 30% into query vector)
        ├─ mesh_preferred[:5]  → _lexical_search() (OR'd into tsquery)
        ├─ matched_mesh_ids  → _mesh_only_search() (direct gse_mesh lookup)
        └─ mesh_concept_groups  → AND logic across concept groups
```
