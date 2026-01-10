"""
Milvus vector store for GEO embeddings.
Manages collection creation, upsertion, and similarity search.
"""
import logging
from typing import Any

from pymilvus import (
    Collection,
    CollectionSchema,
    DataType,
    FieldSchema,
    MilvusException,
    connections,
    utility,
)

from config import settings
from vector.embeddings import get_embedding_provider

logger = logging.getLogger(__name__)


class MilvusStore:
    """
    Milvus vector store for GEO dataset embeddings.
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        collection_name: str | None = None,
    ):
        """
        Initialize Milvus store.

        Args:
            host: Milvus server host
            port: Milvus server port
            collection_name: Collection name for GEO embeddings
        """
        self.host = host or settings.milvus_host
        self.port = port or settings.milvus_port
        self.collection_name = collection_name or settings.milvus_collection_name

        # Get embedding dimension
        embedding_provider = get_embedding_provider()
        self.dimension = embedding_provider.get_dimension()

        logger.info(
            f"Initializing Milvus store: {self.host}:{self.port}, "
            f"collection={self.collection_name}, dim={self.dimension}"
        )

        # Connect to Milvus
        self._connect()

        # Ensure collection exists
        self._ensure_collection()

    def _connect(self) -> None:
        """Connect to Milvus server."""
        try:
            connections.connect(
                alias="default",
                host=self.host,
                port=str(self.port),
            )
            logger.info("Connected to Milvus successfully")
        except MilvusException as e:
            logger.error(f"Failed to connect to Milvus: {e}")
            raise

    def _ensure_collection(self) -> None:
        """
        Ensure collection exists, create if not.
        """
        if utility.has_collection(self.collection_name):
            logger.info(f"Collection '{self.collection_name}' exists")
            self.collection = Collection(self.collection_name)

            # Load collection into memory for search
            self.collection.load()
        else:
            logger.info(f"Creating collection '{self.collection_name}'")
            self._create_collection()

    def _create_collection(self) -> None:
        """
        Create Milvus collection with schema.
        """
        # Define schema
        fields = [
            FieldSchema(
                name="accession",
                dtype=DataType.VARCHAR,
                is_primary=True,
                max_length=20,
            ),
            FieldSchema(
                name="embedding",
                dtype=DataType.FLOAT_VECTOR,
                dim=self.dimension,
            ),
        ]

        schema = CollectionSchema(
            fields=fields,
            description="GEO Series embeddings for semantic search",
        )

        # Create collection
        self.collection = Collection(
            name=self.collection_name,
            schema=schema,
        )

        # Create IVF_FLAT index for similarity search
        index_params = {
            "metric_type": "IP",  # Inner Product (cosine similarity)
            "index_type": "IVF_FLAT",
            "params": {"nlist": 1024},
        }

        self.collection.create_index(
            field_name="embedding",
            index_params=index_params,
        )

        logger.info(f"Created collection '{self.collection_name}' with index")

        # Load collection
        self.collection.load()

    def upsert_embeddings(
        self,
        embeddings: list[tuple[str, list[float]]],
    ) -> None:
        """
        Insert or update embeddings in Milvus.

        Args:
            embeddings: List of (accession, embedding_vector) tuples

        Example:
            >>> store = MilvusStore()
            >>> store.upsert_embeddings([
            ...     ("GSE123456", [0.1, 0.2, ...]),
            ...     ("GSE123457", [0.3, 0.4, ...]),
            ... ])
        """
        if not embeddings:
            logger.warning("No embeddings to upsert")
            return

        accessions = [e[0] for e in embeddings]
        vectors = [e[1] for e in embeddings]

        logger.info(f"Upserting {len(embeddings)} embeddings")

        try:
            # Milvus automatically handles upserts based on primary key
            data = [
                accessions,
                vectors,
            ]

            self.collection.insert(data)
            self.collection.flush()

            logger.info(f"Successfully upserted {len(embeddings)} embeddings")

        except MilvusException as e:
            logger.error(f"Failed to upsert embeddings: {e}")
            raise

    def search(
        self,
        query_vector: list[float],
        top_k: int = 100,
        filter_expr: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Search for similar embeddings.

        Args:
            query_vector: Query embedding vector
            top_k: Number of results to return
            filter_expr: Optional filter expression (Milvus syntax)

        Returns:
            List of results with accession and similarity score

        Example:
            >>> results = store.search(
            ...     query_vector=[0.1, 0.2, ...],
            ...     top_k=50,
            ... )
            >>> for result in results:
            ...     print(f"{result['accession']}: {result['score']}")
        """
        if not query_vector:
            return []

        search_params = {
            "metric_type": "IP",  # Inner Product
            "params": {"nprobe": 10},
        }

        logger.debug(f"Searching Milvus: top_k={top_k}, filter={filter_expr}")

        try:
            results = self.collection.search(
                data=[query_vector],
                anns_field="embedding",
                param=search_params,
                limit=top_k,
                expr=filter_expr,
                output_fields=["accession"],
            )

            # Format results
            formatted = []
            for hits in results:
                for hit in hits:
                    formatted.append({
                        "accession": hit.entity.get("accession"),
                        "score": float(hit.score),
                    })

            logger.info(f"Found {len(formatted)} results")
            return formatted

        except MilvusException as e:
            logger.error(f"Search failed: {e}")
            # Return empty results on error instead of failing
            return []

    def delete(self, accessions: list[str]) -> None:
        """
        Delete embeddings by accession.

        Args:
            accessions: List of GSE accessions to delete
        """
        if not accessions:
            return

        expr = f"accession in {accessions}"
        logger.info(f"Deleting {len(accessions)} embeddings")

        try:
            self.collection.delete(expr)
            self.collection.flush()
        except MilvusException as e:
            logger.error(f"Delete failed: {e}")
            raise

    def count(self) -> int:
        """
        Get total number of embeddings in collection.

        Returns:
            Count of embeddings
        """
        try:
            return self.collection.num_entities
        except MilvusException as e:
            logger.error(f"Count failed: {e}")
            return 0

    def drop_collection(self) -> None:
        """
        Drop the entire collection.
        WARNING: This deletes all data!
        """
        logger.warning(f"Dropping collection '{self.collection_name}'")
        try:
            utility.drop_collection(self.collection_name)
            logger.info("Collection dropped")
        except MilvusException as e:
            logger.error(f"Failed to drop collection: {e}")
            raise
