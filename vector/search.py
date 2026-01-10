"""Semantic search utilities."""
import logging
from typing import Any

from vector.embeddings import get_embedding_provider
from vector.milvus_store import MilvusStore

logger = logging.getLogger(__name__)


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

    # Generate query embedding
    embedding_provider = get_embedding_provider()
    query_embedding = embedding_provider.embed_texts([query])[0]

    # Search in Milvus
    vector_store = MilvusStore()
    results = vector_store.search(
        query_vector=query_embedding,
        top_k=top_k,
        filter_expr=filter_expr,
    )

    logger.info(f"Semantic search returned {len(results)} results")
    return results
