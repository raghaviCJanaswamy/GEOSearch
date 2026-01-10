"""Pytest configuration and fixtures."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from db.models import Base


@pytest.fixture(scope="function")
def test_db():
    """Create a test database."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    yield session

    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture
def sample_gse_data():
    """Sample GSE data for testing."""
    return {
        "accession": "GSE123456",
        "title": "Test RNA-seq study of breast cancer",
        "summary": "This is a comprehensive RNA-seq analysis",
        "overall_design": "Case-control study design",
        "organism_text": "Homo sapiens",
        "organisms": ["Homo sapiens"],
        "platforms": ["GPL1234"],
        "tech_type": "rna-seq",
        "pubmed_ids": ["12345678"],
        "sample_count": 20,
    }
