"""
Pluggable embedding provider interface.
Supports local sentence-transformers and OpenAI embeddings.
"""
import logging
from abc import ABC, abstractmethod
from typing import Any

from config import settings

logger = logging.getLogger(__name__)


class EmbeddingProvider(ABC):
    """Abstract base class for embedding providers."""

    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """
        Generate embeddings for a list of texts.

        Args:
            texts: List of text strings

        Returns:
            List of embedding vectors (each vector is a list of floats)
        """
        pass

    @abstractmethod
    def get_dimension(self) -> int:
        """
        Get the dimensionality of embeddings produced by this provider.

        Returns:
            Embedding dimension
        """
        pass


class LocalEmbeddingProvider(EmbeddingProvider):
    """
    Local embedding provider using sentence-transformers.
    Runs models locally without external API calls.
    """

    def __init__(self, model_name: str | None = None):
        """
        Initialize local embedding provider.

        Args:
            model_name: Sentence-transformers model name
                       (default: from settings)
        """
        self.model_name = model_name or settings.embedding_model

        logger.info(f"Loading local embedding model: {self.model_name}")

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "sentence-transformers not installed. "
                "Install with: pip install sentence-transformers"
            )

        self.model = SentenceTransformer(self.model_name)
        self._dimension = self.model.get_sentence_embedding_dimension()

        logger.info(
            f"Loaded model '{self.model_name}' (dimension: {self._dimension})"
        )

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings using local model."""
        if not texts:
            return []

        logger.debug(f"Embedding {len(texts)} texts with local model")

        # Encode texts
        embeddings = self.model.encode(
            texts,
            convert_to_numpy=True,
            show_progress_bar=False,
            batch_size=32,
        )

        # Convert to list of lists
        return embeddings.tolist()

    def get_dimension(self) -> int:
        """Get embedding dimension."""
        return self._dimension


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """
    OpenAI embedding provider using their API.
    Requires OPENAI_API_KEY to be set.
    """

    DIMENSION_MAP = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
    ):
        """
        Initialize OpenAI embedding provider.

        Args:
            api_key: OpenAI API key (default: from settings)
            model: OpenAI embedding model name (default: from settings)
        """
        self.api_key = api_key or settings.openai_api_key
        self.model = model or settings.openai_embedding_model

        if not self.api_key:
            raise ValueError(
                "OpenAI API key required. Set OPENAI_API_KEY environment variable."
            )

        logger.info(f"Initializing OpenAI embedding provider: model={self.model}")

        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError(
                "openai package not installed. "
                "Install with: pip install openai"
            )

        self.client = OpenAI(api_key=self.api_key)
        self._dimension = self.DIMENSION_MAP.get(self.model, 1536)

        logger.info(f"OpenAI provider ready (dimension: {self._dimension})")

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings using OpenAI API."""
        if not texts:
            return []

        logger.debug(f"Embedding {len(texts)} texts with OpenAI API")

        try:
            response = self.client.embeddings.create(
                model=self.model,
                input=texts,
            )

            embeddings = [item.embedding for item in response.data]
            return embeddings

        except Exception as e:
            logger.error(f"OpenAI embedding failed: {e}")
            raise

    def get_dimension(self) -> int:
        """Get embedding dimension."""
        return self._dimension


def get_embedding_provider() -> EmbeddingProvider:
    """
    Factory function to get the configured embedding provider.

    Returns:
        Configured EmbeddingProvider instance

    Raises:
        ValueError: If provider configuration is invalid
    """
    provider_type = settings.embedding_provider

    if provider_type == "local":
        return LocalEmbeddingProvider()
    elif provider_type == "openai":
        return OpenAIEmbeddingProvider()
    else:
        raise ValueError(
            f"Unknown embedding provider: {provider_type}. "
            f"Must be 'local' or 'openai'"
        )
