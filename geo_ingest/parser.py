"""
Parser for GEO metadata into structured format.
Normalizes and cleans data fetched from NCBI.
"""
import logging
import re
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


class GEOParser:
    """Parser for GEO Series metadata."""

    # Technology type keywords for classification
    TECH_KEYWORDS = {
        "rna-seq": ["rna-seq", "rna seq", "rnaseq", "transcriptome sequencing"],
        "single-cell": ["single-cell", "single cell", "scrna-seq", "scrnaseq", "10x genomics"],
        "chip-seq": ["chip-seq", "chip seq", "chipseq", "chromatin immunoprecipitation"],
        "microarray": ["microarray", "array", "affymetrix", "agilent", "illumina beadarray"],
        "atac-seq": ["atac-seq", "atac seq", "atacseq"],
        "methylation": ["methylation", "bisulfite", "wgbs", "rrbs"],
        "wgs": ["whole genome sequencing", "wgs", "genome sequencing"],
        "wes": ["whole exome sequencing", "wes", "exome sequencing"],
        "other-seq": ["sequencing", "-seq"],
    }

    @staticmethod
    def parse_gse_metadata(raw_data: dict[str, Any]) -> dict[str, Any]:
        """
        Parse raw GEO metadata into normalized structure.

        Args:
            raw_data: Raw metadata dictionary from NCBI client

        Returns:
            Normalized metadata dictionary ready for database storage
        """
        if "error" in raw_data:
            logger.warning(f"Skipping {raw_data.get('accession')} due to error: {raw_data['error']}")
            return {}

        accession = raw_data.get("accession", "")
        if not accession or not accession.startswith("GSE"):
            logger.warning(f"Invalid accession: {accession}")
            return {}

        # Extract and clean fields
        title = GEOParser._clean_text(raw_data.get("title", ""))
        summary = GEOParser._clean_text(raw_data.get("summary", ""))
        overall_design = GEOParser._clean_text(raw_data.get("overall_design", ""))

        # Organism normalization
        organisms_raw = raw_data.get("organisms", [])
        organism_text = raw_data.get("taxon", "")
        if not organisms_raw and organism_text:
            organisms_raw = [organism_text]

        organisms = GEOParser._normalize_organisms(organisms_raw)

        # Platform IDs
        platforms = raw_data.get("platform_ids", [])

        # PubMed IDs
        pubmed_ids = [str(pmid) for pmid in raw_data.get("pubmed_ids", []) if pmid]

        # Technology type inference
        combined_text = f"{title} {summary} {overall_design}".lower()
        tech_type = GEOParser._infer_tech_type(combined_text)

        # Sample count
        sample_count = GEOParser._parse_int(raw_data.get("n_samples"))

        # Dates
        submission_date = GEOParser._parse_date(raw_data.get("submission_date"))
        last_update_date = GEOParser._parse_date(raw_data.get("entrez_date"))

        parsed = {
            "accession": accession,
            "title": title,
            "summary": summary,
            "overall_design": overall_design,
            "organism_text": "; ".join(organisms_raw) if organisms_raw else "",
            "organisms": organisms,
            "platforms": platforms,
            "tech_type": tech_type,
            "pubmed_ids": pubmed_ids,
            "submission_date": submission_date,
            "last_update_date": last_update_date,
            "sample_count": sample_count,
            "raw_record": raw_data,
        }

        logger.debug(f"Parsed {accession}: tech_type={tech_type}, organisms={organisms}")
        return parsed

    @staticmethod
    def _clean_text(text: str) -> str:
        """Clean and normalize text fields."""
        if not text:
            return ""
        # Remove extra whitespace
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    @staticmethod
    def _normalize_organisms(organisms: list[str]) -> list[str]:
        """
        Normalize organism names.

        Common organisms:
        - Homo sapiens
        - Mus musculus
        - Rattus norvegicus
        - Drosophila melanogaster
        - etc.
        """
        normalized = []
        for org in organisms:
            org = org.strip()
            if not org:
                continue

            # Basic normalization - convert to title case
            org_lower = org.lower()

            # Map common variations
            mappings = {
                "human": "Homo sapiens",
                "mouse": "Mus musculus",
                "rat": "Rattus norvegicus",
                "fly": "Drosophila melanogaster",
                "worm": "Caenorhabditis elegans",
                "yeast": "Saccharomyces cerevisiae",
                "zebrafish": "Danio rerio",
            }

            normalized_org = mappings.get(org_lower, org)
            normalized.append(normalized_org)

        return list(set(normalized))  # Remove duplicates

    @staticmethod
    def _infer_tech_type(text: str) -> str:
        """
        Infer technology type from text content.

        Priority order: single-cell > rna-seq > chip-seq > other specific > microarray > other-seq
        """
        text_lower = text.lower()

        # Check in priority order
        priority_order = [
            "single-cell",
            "rna-seq",
            "chip-seq",
            "atac-seq",
            "methylation",
            "wgs",
            "wes",
            "other-seq",
            "microarray",
        ]

        for tech in priority_order:
            keywords = GEOParser.TECH_KEYWORDS.get(tech, [])
            for keyword in keywords:
                if keyword in text_lower:
                    return tech

        return "unknown"

    @staticmethod
    def _parse_int(value: Any) -> int | None:
        """Safely parse integer value."""
        if value is None:
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_date(date_str: str | None) -> datetime | None:
        """
        Parse date string to datetime object.

        Handles various GEO date formats:
        - YYYY/MM/DD
        - YYYY-MM-DD
        - YYYYMMDD
        """
        if not date_str:
            return None

        # Try different formats
        formats = [
            "%Y/%m/%d",
            "%Y-%m-%d",
            "%Y%m%d",
            "%Y/%m/%d %H:%M",
            "%Y-%m-%d %H:%M:%S",
        ]

        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue

        logger.warning(f"Could not parse date: {date_str}")
        return None

    @staticmethod
    def prepare_embedding_text(metadata: dict[str, Any]) -> str:
        """
        Prepare concatenated text for embedding generation.

        Combines title, summary, and overall design with appropriate weighting.

        Args:
            metadata: Parsed metadata dictionary

        Returns:
            Text string ready for embedding
        """
        parts = []

        # Title (most important, include twice for weighting)
        title = metadata.get("title", "")
        if title:
            parts.append(title)
            parts.append(title)

        # Summary
        summary = metadata.get("summary", "")
        if summary:
            parts.append(summary)

        # Overall design
        overall_design = metadata.get("overall_design", "")
        if overall_design:
            parts.append(overall_design)

        # Add organism context
        organisms = metadata.get("organisms", [])
        if organisms:
            parts.append(f"Organism: {', '.join(organisms)}")

        # Add technology type
        tech_type = metadata.get("tech_type", "")
        if tech_type and tech_type != "unknown":
            parts.append(f"Technology: {tech_type}")

        text = " ".join(parts)
        return GEOParser._clean_text(text)
