"""Semantic search utilities."""
import logging
from typing import Any

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
    top_k: int = 100,
    filter_expr: str | None = None,
) -> list[dict[str, Any]]:
    """
    Perform semantic search over GEO datasets.

    Args:
        query: Search query text
        top_k: Number of results to return
        filter_expr: Optional Milvus filter expression

    Returns:
        List of search results with accession and score

    Example:
        >>> results = semantic_search("breast cancer RNA-seq", top_k=50)
        >>> for result in results:
        ...     print(f"{result['accession']}: {result['score']:.3f}")
    """
    logger.info(f"Semantic search: query='{query}', top_k={top_k}")

    embedding_provider = _get_embedding_provider()
    query_embedding = embedding_provider.embed_texts([query])[0]

    vector_store = _get_vector_store()
    results = vector_store.search(
        query_vector=query_embedding,
        top_k=top_k,
        filter_expr=filter_expr,
    )

    logger.info(f"Semantic search returned {len(results)} results")
    return results
