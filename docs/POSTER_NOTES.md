# GEOSearch — Poster Presentation Notes
## AI Symposium — Speaker Reference Card

---

## YOUR 30-SECOND OPENER

> "NCBI GEO has over 200,000 genomic datasets, but finding the right one is hard because
> researchers search in plain English while scientists label data in medical terminology.
> GEOSearch fixes that by combining three AI techniques — semantic vector search, keyword
> matching, and a full medical vocabulary called MeSH — to return relevant datasets
> regardless of how you phrase your query. For example, searching 'heart attack' now finds
> datasets labeled 'myocardial infarction' automatically."

---

## SECTION-BY-SECTION TALKING POINTS

---

### 1. PROBLEM — What to say

**The core problem (say this clearly)**
- GEO has the data. Researchers can't find it.
- Vocabulary gap: lay terms vs. scientific terms vs. medical jargon
- Example: "heart attack" → 2 results. "myocardial infarction" → 10 results. Same disease, different words.
- Without fixing this, ~50% of relevant prior work gets missed — leading to duplicated experiments.

**If asked: "Why does this matter?"**
> "Missed datasets mean missed insights. If a lab repeats an experiment that's already been done
> and published as a GEO dataset, that's wasted funding and time. GEOSearch reduces that."

---

### 2. ARCHITECTURE — What to say

**Walk through the pipeline top to bottom:**

1. **User types a query** → Streamlit UI
2. **MeSH expansion** → query is enriched with medical synonyms before search
   - "breast cancer" becomes "breast cancer + Breast Neoplasms + mammary carcinoma + BRCA2..."
3. **Two searches run in parallel:**
   - Semantic search (Milvus) — finds datasets with *similar meaning* using AI embeddings
   - Lexical search (PostgreSQL) — finds datasets with *exact keyword matches*
4. **RRF fusion** — ranks by combining both result lists + MeSH boost
5. **Results displayed** with MeSH badges, metadata, and now an **LLM-generated answer**

**Key architecture point to emphasize:**
> "The three methods are complementary. Semantic catches meaning. Lexical catches exact terms.
> MeSH catches medical synonyms. RRF fusion means a dataset that shows up in any two of the
> three gets ranked very highly."

---

### 3. KEY INNOVATIONS — What to say

**Innovation 1: Full MeSH Integration**
- 31,110 medical terms from NLM (National Library of Medicine) — the same vocabulary used by PubMed
- Each term has synonyms (entry terms) — "Myocardial Infarction" has 12 synonyms including "Heart Attack"
- Every dataset automatically tagged with relevant MeSH terms at ingestion time
- At query time, matched MeSH terms boost those datasets in the final ranking

**Innovation 2: Hybrid Search + RRF**
- Formula: `score = Σ 1/(60 + rank)` across all result lists
- Why rank-based: Avoids the problem of normalizing incompatible score scales (cosine vs. BM25)
- Why k=60: Standard value from Cormack et al. 2009, empirically validated across IR benchmarks
- MeSH boost: +0.1 per matched term, capped at +0.5

**Innovation 3: LLM Q&A (RAG)**
- This is beyond the poster — added after the poster was written
- After search retrieves top results, an LLM (local Ollama llama3 or OpenAI GPT-4o-mini) synthesizes a direct answer
- This is Retrieval-Augmented Generation (RAG): search = retrieval, LLM = generation
- Keeps answers grounded — LLM can only cite the actual datasets returned

**Innovation 4: FastAPI REST API**
- Also beyond the poster (listed as future work, but already built)
- Exposes `/search`, `/ask`, `/stats`, `/datasets/{id}` endpoints
- Enables programmatic integration with analysis pipelines

---

### 4. RESULTS — Numbers to memorize

| Metric | Value |
|--------|-------|
| MeSH terms loaded | 31,110 |
| Datasets in system | 5,500+ (pilot started at 70) |
| Query expansion rate | 95% of queries match at least one MeSH term |
| Recall improvement | +30–40% with MeSH vs. keyword-only |
| "Heart attack" results | 2 (keyword) → 23 (with MeSH) |
| Search latency | 100–500ms total |
| MeSH expansion latency | 50–100ms |
| Semantic search latency | 50–150ms |
| Lexical search latency | 20–80ms |

**If asked about evaluation methodology:**
> "We compared result counts for the same query with and without MeSH expansion enabled.
> We defined relevance as: datasets that have a confirmed MeSH association with the query's
> matched terms. Formal human-annotation evaluation is planned as next steps."

---

### 5. TECH STACK — One-liner for each

| Component | One-liner |
|-----------|-----------|
| **Streamlit** | Python web UI, no frontend code needed |
| **PostgreSQL** | Structured metadata + full-text search (tsvector) |
| **Milvus** | Vector database for 384-dim embeddings |
| **sentence-transformers** | Local embedding model, free, trained on PubMed |
| **MeSH 2026** | NLM's medical vocabulary, 31K terms, updated annually |
| **SQLAlchemy** | ORM for database access |
| **Docker Compose** | 8 services orchestrated together |
| **FastAPI** | REST API layer (new) |
| **Ollama / OpenAI** | LLM for Q&A (new) |

---

### 6. COMPARISON TABLE — What to emphasize

**vs. GEO Website:**
> "GEO's own search is pure keyword — no vector similarity, no MeSH expansion,
> no intelligent ranking. If your word isn't in the dataset text, you get nothing."

**vs. PubMed:**
> "PubMed has MeSH but it's manual — curators tag papers by hand. GEOSearch
> does it automatically for every dataset at ingestion time."

**vs. Google Scholar:**
> "Google Scholar has semantic ranking but no biomedical ontology integration,
> no dataset-specific filters (organism, tech type, sample count), and no API."

**Unique advantage:**
> "GEOSearch is the only system that combines all three: semantic vectors + lexical
> matching + full MeSH vocabulary. That combination is the novelty."

---

### 7. LIMITATIONS — Be upfront, have answers

**"Only 70 datasets in your results section"**
> "That was the pilot. The system now has 5,500+ datasets and the architecture
> is validated to 100K+. The percentages (MeSH coverage, query expansion rate)
> don't change with scale — they depend on the MeSH vocabulary, not dataset count."

**"The recall numbers — how rigorous is that?"**
> "The 30-40% is based on comparing result counts with/without MeSH. Rigorous
> evaluation requires human relevance judgments, which is explicitly our next step.
> But the directional improvement is unambiguous — heart attack goes from 2 to 23 results."

**"GEO only — what about SRA or ArrayExpress?"**
> "Yes, currently GEO only. The ingestion pipeline uses NCBI E-utilities which
> supports SRA as well. Extending to SRA is in the roadmap — same architecture applies."

**"Local embeddings vs. OpenAI — are your results good enough?"**
> "all-MiniLM-L6-v2 was trained on PubMed abstracts so it has solid biomedical
> coverage. For production at large scale, OpenAI text-embedding-3-small is a config
> change — we support both. The MeSH layer compensates significantly for embedding
> model limitations."

---

### 8. FUTURE WORK — What to say

**Immediate (3-6 months):**
- Scale to 50K+ datasets
- Add MeSH tree hierarchy navigation (browse by medical category)
- Integrate PubMed data to pull MeSH tags from linked publications

**Medium-term (6-12 months):**
- Multi-database: SRA, ArrayExpress
- Knowledge graph — link datasets sharing MeSH terms
- Fine-tune embeddings on biomedical corpus

**Already done (beyond the poster):**
- FastAPI REST API with `/search` and `/ask` endpoints
- LLM Q&A (RAG) with local Ollama support — no API key needed

---

## ANTICIPATED TOUGH QUESTIONS + ANSWERS

**Q: Is this just a wrapper around GEO's existing API?**
> "No. GEO's API returns metadata. GEOSearch adds three layers on top: it pre-processes
> all that metadata into embedding vectors (stored in Milvus), indexes it for full-text
> search (PostgreSQL tsvector), and links it to the full MeSH vocabulary. The query
> processing, ranking, and now LLM Q&A are entirely our own."

**Q: Why not just use a large language model for the whole thing?**
> "LLMs hallucinate — they'll confidently cite datasets that don't exist. Our
> approach retrieves real, verifiable GEO accessions first, then uses the LLM only
> to synthesize an answer from those verified results. That's RAG, and it's the
> right pattern for factual retrieval tasks."

**Q: How do you handle a query with no MeSH match?**
> "It falls back gracefully to semantic + lexical search with the original query.
> The MeSH layer adds value when it matches, but never blocks results when it doesn't."

**Q: What's k=60 in RRF and why that number?**
> "k is a damping constant that controls how much low-ranked results are penalized.
> 60 is the standard value from the original Cormack 2009 paper, validated across
> many information retrieval benchmarks. We didn't tune it — using the established
> value is the right call for a new system."

**Q: How is MeSH confidence scored?**
> "We use a weighted dictionary match. Exact phrase match in the title gets the
> highest weight (2x), summary gets 1.5x, overall design gets 1x. Longer matching
> phrases get higher confidence than single words. Minimum threshold is 0.3 to
> filter out coincidental single-word matches."

**Q: Can a researcher use this for their own institution's data?**
> "Yes — the system is fully containerized via Docker Compose. An institution
> could ingest their own private GEO-format datasets and run GEOSearch on-premise.
> The local embedding model means no data ever leaves the institution."

---

## LIVE DEMO SCRIPT (if you have a laptop)

1. Open http://localhost:8501
2. Search: **"heart attack"** with MeSH ON
   - Show 20+ results, point to MeSH badges (Myocardial Infarction, etc.)
3. Toggle MeSH OFF, search again
   - Show ~2 results — dramatic difference
4. Toggle MeSH back ON, search: **"breast cancer RNA-seq"**
   - Show AI Answer panel at the top (LLM synthesized response)
   - Point to MeSH expander showing detected terms
5. Show sidebar filters — organism, tech type, date range
6. If time: open http://localhost:8080/docs — show FastAPI Swagger UI, run `/ask`

---

## CLOSING STATEMENT

> "GEOSearch demonstrates that combining three complementary AI techniques —
> semantic embeddings, full-text search, and medical ontology — produces substantially
> better biomedical dataset discovery than any single method alone. The system is
> open-source, fully containerized, and now includes an LLM Q&A layer that lets
> researchers ask plain English questions and get answers grounded in real datasets.
> We believe this architecture is directly applicable to other biomedical databases
> beyond GEO."

---

*Reference: full poster at docs/POSTER_PRESENTATION.md*
*Gap analysis: docs/POSTER_NOTES.md (this file)*
