"""Semantic search utilities."""
import logging
from typing import Any

import numpy as np

from vector.embeddings import EmbeddingProvider, get_embedding_provider
from vector.milvus_store import MilvusStore

logger = logging.getLogger(__name__)

# Module-level singletons — loaded once per process, reused on every search call.
# Avoids reloading the ~90MB sentence-transformer model and reconnecting to
# Milvus on every request.
_embedding_provider: EmbeddingProvider | None = None
_vector_store: MilvusStore | None = None


def _get_embedding_provider() -> EmbeddingProvider:
    global _embedding_provider
    if _embedding_provider is None:
        _embedding_provider = get_embedding_provider()
    return _embedding_provider


def _get_vector_store() -> MilvusStore:
    global _vector_store
    if _vector_store is None:
        _vector_store = MilvusStore()
    return _vector_store


def semantic_search(
    query: str,
    top_k: int = 500,
    filter_expr: str | None = None,
    min_score: float = 0.45,
    expanded_query: str | None = None,
    query_weight: float = 0.7,
) -> list[dict[str, Any]]:
    """
    Perform semantic search over GEO datasets.

    Args:
        query: Original user query (always embedded)
        top_k: Number of results to return
        filter_expr: Optional Milvus filter expression
        min_score: Minimum cosine similarity threshold
        expanded_query: MeSH-expanded query text (embedded separately and blended)
        query_weight: Weight for original query vector (1 - query_weight goes to expanded)

    When expanded_query is provided the final search vector is a weighted average:
        v_final = query_weight * v_query + (1 - query_weight) * v_expanded
    This prevents long MeSH synonym lists from diluting the original query intent.
    """
    logger.info(f"Semantic search: query='{query}', top_k={top_k}, expanded={bool(expanded_query)}")

    embedding_provider = _get_embedding_provider()

    if expanded_query and expanded_query.strip() != query.strip():
        # Embed original and expanded separately, then weighted-average
        vectors = embedding_provider.embed_texts([query, expanded_query])
        v_query = np.array(vectors[0], dtype=np.float32)
        v_expanded = np.array(vectors[1], dtype=np.float32)
        blended = query_weight * v_query + (1.0 - query_weight) * v_expanded
        # Re-normalise so cosine similarity (inner product on unit vectors) stays valid
        norm = np.linalg.norm(blended)
        query_embedding = (blended / norm).tolist() if norm > 0 else blended.tolist()
        logger.info(f"Blended query vector: {query_weight:.0%} original + {1-query_weight:.0%} expanded")
    else:
        query_embedding = embedding_provider.embed_texts([query])[0]

    vector_store = _get_vector_store()
    results = vector_store.search(
        query_vector=query_embedding,
        top_k=top_k,
        filter_expr=filter_expr,
    )

    # Filter out low-confidence semantic matches to avoid inflating result counts.
    if min_score > 0:
        results = [r for r in results if r["score"] >= min_score]

    logger.info(f"Semantic search returned {len(results)} results (min_score={min_score})")
    return results
