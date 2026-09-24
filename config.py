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
    postgres_password: str = Field(default="", description="Set via POSTGRES_PASSWORD env var")
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
    embedding_provider: Literal["local", "openai", "azure_openai"] = "local"
    embedding_model: str = "NeuML/pubmedbert-base-embeddings"
    embedding_dimension: int = 768

    # OpenAI
    openai_api_key: str | None = None
    openai_embedding_model: str = "text-embedding-3-small"

    # Azure OpenAI
    azure_openai_api_key: str | None = None
    azure_openai_endpoint: str | None = None
    azure_openai_api_version: str = "2024-02-01"
    azure_openai_deployment: str | None = None

    # LLM Q&A
    llm_provider: str = "auto"           # "auto" | "ollama" | "openai" | "none"
    llm_model: str | None = None         # override model name per provider
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3"         # model tag pulled in Ollama

    # Search
    semantic_top_k: int = 1000  # Milvus candidates — raised to 1000 so high-volume queries
                                 # (e.g. NFkB activation, triple-negative breast cancer) surface
                                 # relevant datasets beyond the former 500-candidate ceiling.
                                 # The adaptive threshold cascade (0.65→0.45) still controls
                                 # which candidates enter RRF, so ranking quality is preserved.
    lexical_top_k: int = 100
    final_top_k: int = 500
    rrf_k: int = 60  # Reciprocal Rank Fusion constant

    # MeSH tagging
    mesh_confidence_threshold: float = 0.3

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
