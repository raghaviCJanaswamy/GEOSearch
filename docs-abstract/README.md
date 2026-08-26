# GEOSearch — Abstract Documentation

Plain-language technical abstracts for each major system component. These describe **what the code actually does**, grounded in the current implementation.

---

## Documents

| File | Covers |
|------|--------|
| [semanticSearch.md](semanticSearch.md) | End-to-end semantic search: PubMedBERT embedding, Milvus ANN, adaptive threshold cascade, RRF input |
| [meshExpansion.md](meshExpansion.md) | MeSH query expansion: 5-pass matching, blocklists, lay term aliases, multi-concept AND logic |
| [lexicalSearch.md](lexicalSearch.md) | PostgreSQL full-text search: weighted fields, prefix matching, cancer synonym expansion, MeSH OR injection |
| [rrfFusion.md](rrfFusion.md) | Reciprocal Rank Fusion: formula, weights (semantic 2×), MeSH boost, why RRF over score normalisation |
| [meshTagging.md](meshTagging.md) | MeSH auto-tagging: two-pass dictionary matching, confidence scoring, field weights, embedding enrichment |
| [ingestionPipeline.md](ingestionPipeline.md) | Ingestion: NCBI fetch → parse → PostgreSQL → Milvus embedding → MeSH tagging |
| [rerankingDesign.md](rerankingDesign.md) | Re-ranking design evaluation: CSV+ChatGPT approach vs. cross-encoder and pre-search LLM query understanding |
| [statisticalAnalysis.md](statisticalAnalysis.md) | Statistical comparison framework: P@k, MRR, NDCG, Wilcoxon test, ablation study vs. NCBI |

---

## Key Facts (Quick Reference)

| Component | Value |
|-----------|-------|
| Embedding model | `NeuML/pubmedbert-base-embeddings` (PubMedBERT, 768-dim) |
| Vector normalisation | `normalize_embeddings=True` — unit-length vectors, IP = cosine similarity |
| Vector store | Milvus (FLAT index, Inner Product metric) |
| Milvus candidates per query | 500 (`semantic_top_k` in `config.py`) |
| Milvus call floor threshold | 0.45 (single call, cascade filtered in-memory) |
| Threshold cascade | 0.65 → 0.60 → 0.50 → 0.45, stops when ≥50 results pass |
| Query blend | 70% original query + 30% MeSH-expanded query, re-normalised |
| RRF k constant | 60 (`rrf_k` in `config.py`) |
| RRF weights | Semantic 2×, Lexical 1×, MeSH-only 1× |
| MeSH boost | +0.1 per matching tag, max +0.5 |
| MeSH terms loaded | ~30,000 descriptors |
| MeSH confidence threshold | 0.3 (`mesh_confidence_threshold` in `config.py`) |
| Relational store | PostgreSQL (SQLAlchemy ORM) |
| Embedding text cap | 2000 chars (PubMedBERT 512-token limit) |
