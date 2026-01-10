"""Tests for GEO metadata parser."""
import pytest
from datetime import datetime

from geo_ingest.parser import GEOParser


class TestGEOParser:
    """Test suite for GEOParser."""

    def test_parse_gse_metadata_basic(self):
        """Test basic metadata parsing."""
        raw_data = {
            "accession": "GSE123456",
            "title": "Test RNA-seq study",
            "summary": "This is a test summary",
            "overall_design": "Test design",
            "organisms": ["Homo sapiens"],
            "platform_ids": ["GPL1234"],
            "pubmed_ids": ["12345678"],
            "n_samples": "10",
            "submission_date": "2023/01/15",
        }

        parsed = GEOParser.parse_gse_metadata(raw_data)

        assert parsed["accession"] == "GSE123456"
        assert parsed["title"] == "Test RNA-seq study"
        assert parsed["tech_type"] == "rna-seq"
        assert parsed["sample_count"] == 10
        assert isinstance(parsed["submission_date"], datetime)

    def test_infer_tech_type_rna_seq(self):
        """Test technology type inference for RNA-seq."""
        text = "RNA-seq analysis of breast cancer samples"
        tech = GEOParser._infer_tech_type(text)
        assert tech == "rna-seq"

    def test_infer_tech_type_single_cell(self):
        """Test technology type inference for single-cell."""
        text = "single-cell RNA sequencing of tumor cells"
        tech = GEOParser._infer_tech_type(text)
        assert tech == "single-cell"

    def test_infer_tech_type_microarray(self):
        """Test technology type inference for microarray."""
        text = "Affymetrix microarray analysis"
        tech = GEOParser._infer_tech_type(text)
        assert tech == "microarray"

    def test_normalize_organisms(self):
        """Test organism normalization."""
        organisms = ["human", "Homo sapiens", "mouse"]
        normalized = GEOParser._normalize_organisms(organisms)

        assert "Homo sapiens" in normalized
        assert "Mus musculus" in normalized
        assert len(normalized) == 2  # Deduplicated

    def test_parse_date_various_formats(self):
        """Test date parsing with different formats."""
        assert GEOParser._parse_date("2023/01/15") is not None
        assert GEOParser._parse_date("2023-01-15") is not None
        assert GEOParser._parse_date("20230115") is not None
        assert GEOParser._parse_date("invalid") is None

    def test_prepare_embedding_text(self):
        """Test embedding text preparation."""
        metadata = {
            "title": "Test Title",
            "summary": "Test summary",
            "overall_design": "Test design",
            "organisms": ["Homo sapiens"],
            "tech_type": "rna-seq",
        }

        text = GEOParser.prepare_embedding_text(metadata)

        assert "Test Title" in text
        assert "Test summary" in text
        assert "Homo sapiens" in text
        assert "rna-seq" in text

    def test_clean_text(self):
        """Test text cleaning."""
        dirty = "  Test   text\n\twith   whitespace  "
        clean = GEOParser._clean_text(dirty)
        assert clean == "Test text with whitespace"
