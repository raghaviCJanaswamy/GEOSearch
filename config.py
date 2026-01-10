"""
Central configuration management using pydantic-settings.
Loads configuration from environment variables and .env file.
"""
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "geosearch"
    postgres_user: str = "geouser"
    postgres_password: str = "geopass"
    postgres_dsn: str | None = None

    # Milvus
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_collection_name: str = "geo_gse_embeddings"

    # NCBI E-utilities
    ncbi_email: str = Field(default="user@example.com")
    ncbi_tool: str = "GEOSearch"
    ncbi_api_key: str | None = None
    rate_limit_qps: float = 3.0  # Queries per second

    # Embeddings
    embedding_provider: Literal["local", "openai"] = "local"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dimension: int = 384

    # OpenAI
    openai_api_key: str | None = None
    openai_embedding_model: str = "text-embedding-3-small"

    # Search
    semantic_top_k: int = 100
    lexical_top_k: int = 100
    final_top_k: int = 50
    rrf_k: int = 60  # Reciprocal Rank Fusion constant

    # Logging
    log_level: str = "INFO"

    # Streamlit
    streamlit_server_port: int = 8501
    streamlit_server_address: str = "0.0.0.0"

    @property
    def database_url(self) -> str:
        """Construct PostgreSQL connection URL."""
        if self.postgres_dsn:
            return self.postgres_dsn
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def ncbi_base_url(self) -> str:
        """NCBI E-utilities base URL."""
        return "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


# Global settings instance
settings = Settings()
