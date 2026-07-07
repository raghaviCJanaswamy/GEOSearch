"""
MeSH term matcher for GEO datasets.
Automatically tags datasets with relevant MeSH terms.
"""
import logging
import re
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from db.models import GSEMesh, GSESeries, MeshTerm

logger = logging.getLogger(__name__)


class MeSHMatcher:
    """
    MeSH term matcher using dictionary-based matching.
    Matches MeSH terms against dataset text fields.
    """

    # Class-level cache so MeSH terms are only loaded once per process
    _term_lookup_cache: dict[str, list[str]] | None = None

    def __init__(self, db: Session):
        """
        Initialize matcher.

    def __init__(self, db: Session):
        self.db = db

        # Load MeSH terms into memory for faster matching (cached at class level)
        if MeSHMatcher._term_lookup_cache is None:
            self._load_mesh_terms()
        self.term_lookup = MeSHMatcher._term_lookup_cache

    def _load_mesh_terms(self) -> None:
        """Load all MeSH terms from database into class-level cache."""
        logger.info("Loading MeSH terms for matching...")
        terms = self.db.query(MeshTerm).all()

        # Build lookup dictionary: term_text -> mesh_id
        term_lookup: dict[str, list[str]] = {}

        for term in terms:
            # Add preferred name
            preferred = term.preferred_name.lower()
            if preferred not in term_lookup:
                term_lookup[preferred] = []
            term_lookup[preferred].append(term.mesh_id)

            # Add entry terms (synonyms)
            if term.entry_terms:
                for entry in term.entry_terms:
                    entry_lower = entry.lower()
                    if entry_lower not in term_lookup:
                        term_lookup[entry_lower] = []
                    term_lookup[entry_lower].append(term.mesh_id)

        MeSHMatcher._term_lookup_cache = term_lookup
        logger.info(f"Loaded {len(terms)} MeSH terms with {len(term_lookup)} searchable variants")

    def match_gse(
        self,
        accession: str,
        confidence_threshold: float = 0.3,
    ) -> list[dict[str, Any]]:
        """
        Find MeSH terms matching a GSE record.

        Args:
            accession: GSE accession
            confidence_threshold: Minimum confidence score (0-1)

        Returns:
            List of matched MeSH terms with confidence scores
        """
        # Get GSE record
        gse = self.db.query(GSESeries).filter(GSESeries.accession == accession).first()
        if not gse:
            logger.warning(f"GSE not found: {accession}")
            return []

        # Combine text fields for matching
        text_fields = []
        if gse.title:
            text_fields.append(("title", gse.title, 2.0))  # Weight
        if gse.summary:
            text_fields.append(("summary", gse.summary, 1.5))
        if gse.overall_design:
            text_fields.append(("design", gse.overall_design, 1.0))

        # Match terms
        matches: dict[str, float] = {}  # mesh_id -> confidence

        for field_name, field_text, weight in text_fields:
            field_matches = self._match_text(field_text, weight)
            for mesh_id, score in field_matches.items():
                if mesh_id in matches:
                    matches[mesh_id] = max(matches[mesh_id], score)
                else:
                    matches[mesh_id] = score

        # Filter by confidence threshold
        filtered = [
            {"mesh_id": mesh_id, "confidence": score}
            for mesh_id, score in matches.items()
            if score >= confidence_threshold
        ]

        # Sort by confidence
        filtered.sort(key=lambda x: x["confidence"], reverse=True)

        logger.info(f"Found {len(filtered)} MeSH matches for {accession}")
        return filtered

    def _match_text(self, text: str, weight: float = 1.0) -> dict[str, float]:
        """Fast two-pass MeSH matching.

        Pass 1 — token lookup: extract words from text, look up each word in
                  single_lookup (O(tokens) dict hits, no scanning).
        Pass 2 — phrase scan: check only multi-word term_lookup keys that
                  start with a word seen in the text (candidate filtering).
        """
        if not text:
            return {}

        text_lower = text.lower()
        matches: dict[str, float] = {}

        # ── Pass 1: single-word token lookup ─────────────────────────────────
        tokens = re.findall(r'\b[a-z]\w{3,}\b', text_lower)  # min 4 chars
        token_set = set(tokens)

        for tok in token_set:
            if tok in self.single_lookup:
                for mesh_id, base_conf in self.single_lookup[tok]:
                    conf = base_conf * weight
                    if mesh_id not in matches or matches[mesh_id] < conf:
                        matches[mesh_id] = conf

        # ── Pass 2: multi-word phrase lookup via first-word index ────────────
        for tok in token_set:
            for phrase, mesh_ids in self.phrase_by_first_word.get(tok, {}).items():
                if phrase in text_lower:
                    n_words = phrase.count(" ") + 1
                    conf = min(1.0, 0.5 + n_words * 0.15) * weight
                    for mesh_id in mesh_ids:
                        if mesh_id not in matches or matches[mesh_id] < conf:
                            matches[mesh_id] = conf

        return matches

    def tag_gse_batch(
        self,
        accessions: list[str],
        confidence_threshold: float = 0.3,
        overwrite: bool = False,
    ) -> int:
        """
        Tag multiple GSE records with MeSH terms.

        Args:
            accessions: List of GSE accessions
            confidence_threshold: Minimum confidence score
            overwrite: If True, delete existing tags first

        Returns:
            Number of associations created
        """
        logger.info(f"Tagging {len(accessions)} GSE records with MeSH terms")

        total_associations = 0

        for accession in accessions:
            # Delete existing if overwrite
            if overwrite:
                self.db.query(GSEMesh).filter(
                    GSEMesh.accession == accession,
                    GSEMesh.source == "auto",
                ).delete()

            # Match and create associations
            matches = self.match_gse(accession, confidence_threshold)

            for match in matches:
                association = GSEMesh(
                    accession=accession,
                    mesh_id=match["mesh_id"],
                    source="auto",
                    confidence=match["confidence"],
                )
                self.db.merge(association)
                total_associations += 1

        self.db.commit()

        logger.info(f"Created {total_associations} MeSH associations")
        return total_associations


def tag_all_gse_records(db: Session, confidence_threshold: float = 0.3) -> int:
    """
    Tag all GSE records in database with MeSH terms.

    Args:
        db: Database session
        confidence_threshold: Minimum confidence score

    Returns:
        Number of associations created
    """
    logger.info("Tagging all GSE records with MeSH terms")

    # Get all accessions
    accessions = [row[0] for row in db.query(GSESeries.accession).all()]

    if not accessions:
        logger.warning("No GSE records found")
        return 0

    matcher = MeSHMatcher(db)
    count = matcher.tag_gse_batch(accessions, confidence_threshold)

    return count
