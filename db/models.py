"""SQLAlchemy ORM models for GEOSearch."""
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()


class GSESeries(Base):
    """
    GEO Series (GSE) metadata table.
    Stores structured and raw metadata for each GSE accession.
    """

    __tablename__ = "gse_series"

    accession = Column(String(20), primary_key=True, index=True)
    title = Column(Text, nullable=False)
    summary = Column(Text)
    overall_design = Column(Text)

    # Organism information
    organism_text = Column(Text)  # Raw organism string
    organisms = Column(JSONB)  # Normalized list of organisms

    # Platform and technology
    platforms = Column(JSONB)  # List of GPL accessions
    tech_type = Column(
        String(50), index=True
    )  # microarray, rna-seq, single-cell, chip-seq, etc.

    # Publication info
    pubmed_ids = Column(JSONB)  # List of PMIDs

    # Dates
    submission_date = Column(DateTime)
    last_update_date = Column(DateTime)

    # Sample information
    sample_count = Column(Integer)

    # Full raw record from NCBI
    raw_record = Column(JSONB)

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    mesh_associations = relationship("GSEMesh", back_populates="gse_series", cascade="all, delete-orphan")

    # Indexes
    __table_args__ = (
        Index("idx_gse_submission_date", "submission_date"),
        Index("idx_gse_tech_type", "tech_type"),
        Index("idx_gse_sample_count", "sample_count"),
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert model to dictionary."""
        return {
            "accession": self.accession,
            "title": self.title,
            "summary": self.summary,
            "overall_design": self.overall_design,
            "organism_text": self.organism_text,
            "organisms": self.organisms,
            "platforms": self.platforms,
            "tech_type": self.tech_type,
            "pubmed_ids": self.pubmed_ids,
            "submission_date": self.submission_date.isoformat() if self.submission_date else None,
            "last_update_date": self.last_update_date.isoformat() if self.last_update_date else None,
            "sample_count": self.sample_count,
        }


class MeshTerm(Base):
    """
    MeSH (Medical Subject Headings) terms dictionary.
    Stores MeSH descriptors with their synonyms/entry terms.
    """

    __tablename__ = "mesh_term"

    mesh_id = Column(String(20), primary_key=True)  # e.g., D001943
    descriptor_ui = Column(String(20), index=True)  # Unique identifier
    preferred_name = Column(String(255), nullable=False, index=True)
    entry_terms = Column(JSONB)  # Array of synonym/entry terms
    tree_numbers = Column(JSONB)  # MeSH tree hierarchy positions

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    gse_associations = relationship("GSEMesh", back_populates="mesh_term")

    __table_args__ = (Index("idx_mesh_preferred_name_lower", "preferred_name"),)


class GSEMesh(Base):
    """
    Association table linking GSE series to MeSH terms.
    Tracks how and why a MeSH term was associated with a dataset.
    """

    __tablename__ = "gse_mesh"

    id = Column(Integer, primary_key=True, autoincrement=True)
    accession = Column(String(20), ForeignKey("gse_series.accession", ondelete="CASCADE"), nullable=False)
    mesh_id = Column(String(20), ForeignKey("mesh_term.mesh_id", ondelete="CASCADE"), nullable=False)

    # Source of association
    source = Column(
        Enum("auto", "pubmed", "manual", name="mesh_source_enum"),
        nullable=False,
        default="auto",
    )

    # Confidence score (0-1)
    confidence = Column(Float, default=0.5)

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    gse_series = relationship("GSESeries", back_populates="mesh_associations")
    mesh_term = relationship("MeshTerm", back_populates="gse_associations")

    __table_args__ = (
        UniqueConstraint("accession", "mesh_id", "source", name="uq_gse_mesh_source"),
        Index("idx_gse_mesh_accession", "accession"),
        Index("idx_gse_mesh_mesh_id", "mesh_id"),
    )


class IngestRun(Base):
    """
    Tracks ingestion runs/jobs.
    Each run can ingest multiple GSE records.
    """

    __tablename__ = "ingest_run"

    id = Column(Integer, primary_key=True, autoincrement=True)
    query = Column(Text)  # NCBI search query used
    start_time = Column(DateTime, default=datetime.utcnow, nullable=False)
    end_time = Column(DateTime)
    status = Column(
        Enum("running", "completed", "failed", "partial", name="ingest_status_enum"),
        default="running",
    )
    total_count = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    error_count = Column(Integer, default=0)
    run_metadata = Column(JSONB)  # Additional run parameters (renamed from 'metadata' to avoid SQLAlchemy conflict)

    # Relationships
    items = relationship("IngestItem", back_populates="run", cascade="all, delete-orphan")


class IngestItem(Base):
    """
    Tracks individual GSE ingestion within a run.
    Provides detailed status and error information per accession.
    """

    __tablename__ = "ingest_item"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("ingest_run.id", ondelete="CASCADE"), nullable=False)
    accession = Column(String(20), nullable=False)  # No FK constraint - record may not exist yet

    status = Column(
        Enum("pending", "fetching", "parsing", "storing", "completed", "failed", name="item_status_enum"),
        default="pending",
    )
    error_message = Column(Text)

    fetch_time = Column(DateTime)
    process_time = Column(DateTime)

    # Relationships
    run = relationship("IngestRun", back_populates="items")

    __table_args__ = (
        Index("idx_ingest_item_run", "run_id"),
        Index("idx_ingest_item_status", "status"),
    )
