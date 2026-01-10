"""
Hybrid search combining semantic, lexical, and MeSH-based search.
Implements Reciprocal Rank Fusion (RRF) for result merging.
"""
import logging
import re
from datetime import datetime
from typing import Any

from sqlalchemy import String, and_, func, or_
from sqlalchemy.orm import Session

from config import settings
from db import GSEMesh, GSESeries, MeshTerm, get_db
from mesh.query_expand import QueryExpander
from vector.search import semantic_search

logger = logging.getLogger(__name__)


class HybridSearchEngine:
    """
    Hybrid search engine combining multiple search strategies.
    """

    def __init__(self, db: Session):
        """
        Initialize hybrid search engine.

        Args:
            db: Database session
        """
        self.db = db
        self.query_expander = QueryExpander(db)

    def search(
        self,
        query: str,
        filters: dict[str, Any] | None = None,
        use_semantic: bool = True,
        use_lexical: bool = True,
        use_mesh: bool = True,
        top_k: int | None = None,
    ) -> dict[str, Any]:
        """
        Perform hybrid search.

        Args:
            query: Search query text
            filters: Structured filters (organisms, tech_type, date_range, min_samples)
            use_semantic: Enable semantic search
            use_lexical: Enable lexical/keyword search
            use_mesh: Enable MeSH expansion
            top_k: Number of results to return

        Returns:
            Dictionary with:
                - results: List of ranked results
                - metadata: Search metadata (query expansion info, etc.)

        Example:
            >>> engine = HybridSearchEngine(db)
            >>> results = engine.search(
            ...     query="breast cancer RNA-seq",
            ...     filters={"organisms": ["Homo sapiens"], "tech_type": "rna-seq"},
            ...     top_k=50,
            ... )
        """
        if top_k is None:
            top_k = settings.final_top_k

        filters = filters or {}

        logger.info(
            f"Hybrid search: query='{query}', "
            f"semantic={use_semantic}, lexical={use_lexical}, mesh={use_mesh}"
        )

        # Step 1: MeSH expansion
        expansion_result = None
        expanded_query = query
        matched_mesh_ids = []

        if use_mesh:
            expansion_result = self.query_expander.expand_query(query)
            expanded_query = expansion_result["expanded_query"]
            matched_mesh_ids = [term["mesh_id"] for term in expansion_result["matched_terms"]]
            logger.info(f"MeSH expansion: {len(matched_mesh_ids)} terms matched")

        # Step 2: Semantic search
        semantic_results = []
        if use_semantic:
            try:
                semantic_results = semantic_search(
                    query=expanded_query,
                    top_k=settings.semantic_top_k,
                )
                logger.info(f"Semantic search: {len(semantic_results)} results")
            except Exception as e:
                logger.error(f"Semantic search failed: {e}", exc_info=True)
                # Continue without semantic results

        # Step 3: Lexical search
        lexical_results = []
        if use_lexical:
            lexical_results = self._lexical_search(
                query=query,
                filters=filters,
                top_k=settings.lexical_top_k,
            )
            logger.info(f"Lexical search: {len(lexical_results)} results")

        # Step 4: Combine results using RRF
        combined_results = self._reciprocal_rank_fusion(
            semantic_results=semantic_results,
            lexical_results=lexical_results,
            matched_mesh_ids=matched_mesh_ids,
        )

        # Step 5: Apply filters and fetch full metadata
        final_results = self._fetch_and_filter_results(
            ranked_accessions=combined_results[:top_k * 2],  # Fetch more for filtering
            filters=filters,
            matched_mesh_ids=matched_mesh_ids,
            top_k=top_k,
        )

        # Prepare metadata
        metadata = {
            "query": query,
            "expanded_query": expanded_query if use_mesh else query,
            "mesh_terms": expansion_result["matched_terms"] if expansion_result else [],
            "semantic_count": len(semantic_results),
            "lexical_count": len(lexical_results),
            "total_results": len(final_results),
            "filters_applied": filters,
        }

        logger.info(f"Hybrid search complete: {len(final_results)} results")

        return {
            "results": final_results,
            "metadata": metadata,
        }

    def _lexical_search(
        self,
        query: str,
        filters: dict[str, Any],
        top_k: int,
    ) -> list[dict[str, Any]]:
        """
        Perform lexical/keyword search using PostgreSQL.

        Args:
            query: Search query
            filters: Structured filters
            top_k: Number of results

        Returns:
            List of results with accession and relevance score
        """
        # Build search conditions
        search_terms = query.lower().split()

        conditions = []
        for term in search_terms:
            if len(term) < 3:
                continue

            term_pattern = f"%{term}%"
            conditions.append(
                or_(
                    func.lower(GSESeries.title).like(term_pattern),
                    func.lower(GSESeries.summary).like(term_pattern),
                    func.lower(GSESeries.overall_design).like(term_pattern),
                )
            )

        if not conditions:
            return []

        # Combine with OR (match any term)
        combined_condition = or_(*conditions)

        # Apply filters
        filter_conditions = self._build_filter_conditions(filters)
        if filter_conditions:
            combined_condition = and_(combined_condition, *filter_conditions)

        # Execute query
        results = (
            self.db.query(GSESeries.accession)
            .filter(combined_condition)
            .limit(top_k)
            .all()
        )

        # Format results (simple scoring based on order)
        formatted = []
        for idx, (accession,) in enumerate(results):
            formatted.append({
                "accession": accession,
                "score": 1.0 / (idx + 1),  # Simple relevance score
            })

        return formatted

    def _build_filter_conditions(self, filters: dict[str, Any]) -> list[Any]:
        """
        Build SQLAlchemy filter conditions from filter dictionary.

        Args:
            filters: Filter parameters

        Returns:
            List of SQLAlchemy conditions
        """
        conditions = []

        # Organism filter
        if organisms := filters.get("organisms"):
            # Match any of the specified organisms
            organism_conditions = []
            for org in organisms:
                organism_conditions.append(
                    func.cast(GSESeries.organisms, String).like(f"%{org}%")
                )
            if organism_conditions:
                conditions.append(or_(*organism_conditions))

        # Technology type filter
        if tech_type := filters.get("tech_type"):
            conditions.append(GSESeries.tech_type == tech_type)

        # Date range filter
        if date_range := filters.get("date_range"):
            start_date = date_range.get("start")
            end_date = date_range.get("end")

            if start_date:
                conditions.append(GSESeries.submission_date >= start_date)
            if end_date:
                conditions.append(GSESeries.submission_date <= end_date)

        # Minimum sample count filter
        if min_samples := filters.get("min_samples"):
            conditions.append(GSESeries.sample_count >= min_samples)

        return conditions

    def _reciprocal_rank_fusion(
        self,
        semantic_results: list[dict[str, Any]],
        lexical_results: list[dict[str, Any]],
        matched_mesh_ids: list[str],
        k: int | None = None,
    ) -> list[str]:
        """
        Combine results using Reciprocal Rank Fusion (RRF).

        RRF score = sum(1 / (k + rank)) across all result lists

        Args:
            semantic_results: Results from semantic search
            lexical_results: Results from lexical search
            matched_mesh_ids: MeSH IDs matched in query expansion
            k: RRF constant (default: from settings)

        Returns:
            List of accessions ranked by RRF score
        """
        if k is None:
            k = settings.rrf_k

        scores: dict[str, float] = {}

        # Add semantic results
        for rank, result in enumerate(semantic_results, start=1):
            accession = result["accession"]
            rrf_score = 1.0 / (k + rank)
            scores[accession] = scores.get(accession, 0.0) + rrf_score

        # Add lexical results
        for rank, result in enumerate(lexical_results, start=1):
            accession = result["accession"]
            rrf_score = 1.0 / (k + rank)
            scores[accession] = scores.get(accession, 0.0) + rrf_score

        # Boost scores for datasets with matching MeSH terms
        if matched_mesh_ids:
            mesh_boost = self._get_mesh_boost_scores(list(scores.keys()), matched_mesh_ids)
            for accession, boost in mesh_boost.items():
                scores[accession] = scores.get(accession, 0.0) + boost

        # Sort by score
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [accession for accession, score in ranked]

    def _get_mesh_boost_scores(
        self,
        accessions: list[str],
        matched_mesh_ids: list[str],
    ) -> dict[str, float]:
        """
        Calculate MeSH-based boost scores for accessions.

        Args:
            accessions: List of GSE accessions
            matched_mesh_ids: MeSH IDs from query expansion

        Returns:
            Dictionary of accession -> boost_score
        """
        if not matched_mesh_ids or not accessions:
            return {}

        # Query GSEMesh associations
        associations = (
            self.db.query(GSEMesh.accession, func.count(GSEMesh.mesh_id))
            .filter(
                GSEMesh.accession.in_(accessions),
                GSEMesh.mesh_id.in_(matched_mesh_ids),
            )
            .group_by(GSEMesh.accession)
            .all()
        )

        # Calculate boost (0.1 per matching MeSH term, max 0.5)
        boost_scores = {}
        for accession, count in associations:
            boost_scores[accession] = min(0.5, count * 0.1)

        return boost_scores

    def _fetch_and_filter_results(
        self,
        ranked_accessions: list[str],
        filters: dict[str, Any],
        matched_mesh_ids: list[str],
        top_k: int,
    ) -> list[dict[str, Any]]:
        """
        Fetch full metadata for ranked results and apply filters.

        Args:
            ranked_accessions: List of accessions in rank order
            filters: Structured filters
            matched_mesh_ids: MeSH IDs for highlighting
            top_k: Number of results to return

        Returns:
            List of result dictionaries with full metadata
        """
        if not ranked_accessions:
            return []

        # Fetch GSE records
        gse_records = (
            self.db.query(GSESeries)
            .filter(GSESeries.accession.in_(ranked_accessions))
            .all()
        )

        # Create lookup
        gse_lookup = {gse.accession: gse for gse in gse_records}

        # Apply filters and format results
        results = []
        for accession in ranked_accessions:
            if accession not in gse_lookup:
                continue

            gse = gse_lookup[accession]

            # Apply filters
            if not self._passes_filters(gse, filters):
                continue

            # Get matched MeSH terms for this dataset
            matched_mesh = []
            if matched_mesh_ids:
                mesh_assocs = (
                    self.db.query(GSEMesh, MeshTerm)
                    .join(MeshTerm, GSEMesh.mesh_id == MeshTerm.mesh_id)
                    .filter(
                        GSEMesh.accession == accession,
                        GSEMesh.mesh_id.in_(matched_mesh_ids),
                    )
                    .all()
                )
                matched_mesh = [
                    {
                        "mesh_id": assoc.mesh_id,
                        "preferred_name": term.preferred_name,
                        "confidence": assoc.confidence,
                    }
                    for assoc, term in mesh_assocs
                ]

            # Format result
            result = {
                **gse.to_dict(),
                "matched_mesh_terms": matched_mesh,
                "geo_url": f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={accession}",
            }

            results.append(result)

            if len(results) >= top_k:
                break

        return results

    def _passes_filters(self, gse: GSESeries, filters: dict[str, Any]) -> bool:
        """
        Check if a GSE record passes all filters.

        Args:
            gse: GSE series object
            filters: Filter dictionary

        Returns:
            True if passes all filters
        """
        # Organism filter
        if organisms := filters.get("organisms"):
            if not gse.organisms:
                return False
            if not any(org in gse.organisms for org in organisms):
                return False

        # Tech type filter
        if tech_type := filters.get("tech_type"):
            if gse.tech_type != tech_type:
                return False

        # Date range filter
        if date_range := filters.get("date_range"):
            if not gse.submission_date:
                return False

            start_date = date_range.get("start")
            end_date = date_range.get("end")

            if start_date and gse.submission_date < start_date:
                return False
            if end_date and gse.submission_date > end_date:
                return False

        # Sample count filter
        if min_samples := filters.get("min_samples"):
            if not gse.sample_count or gse.sample_count < min_samples:
                return False

        return True


def search_geo(
    query: str,
    filters: dict[str, Any] | None = None,
    top_k: int = 50,
    db: Session | None = None,
) -> dict[str, Any]:
    """
    Convenience function for performing GEO search.

    Args:
        query: Search query
        filters: Optional filters
        top_k: Number of results
        db: Optional database session

    Returns:
        Search results dictionary
    """
    if db is None:
        db_gen = get_db()
        db = next(db_gen)
        close_db = True
    else:
        close_db = False

    try:
        engine = HybridSearchEngine(db)
        return engine.search(query=query, filters=filters, top_k=top_k)
    finally:
        if close_db:
            db.close()


def make_snippet(text: str, query_terms: list[str], max_length: int = 200) -> str:
    """
    Create a highlighted snippet from text.

    Args:
        text: Full text
        query_terms: Terms to highlight
        max_length: Maximum snippet length

    Returns:
        Snippet with context around matches
    """
    if not text or not query_terms:
        return text[:max_length] + "..." if len(text) > max_length else text

    text_lower = text.lower()

    # Find first match
    first_match_pos = len(text)
    for term in query_terms:
        pos = text_lower.find(term.lower())
        if pos != -1 and pos < first_match_pos:
            first_match_pos = pos

    if first_match_pos == len(text):
        # No matches, return beginning
        return text[:max_length] + "..." if len(text) > max_length else text

    # Extract context around match
    start = max(0, first_match_pos - 50)
    end = min(len(text), first_match_pos + max_length)

    snippet = text[start:end]

    # Add ellipsis
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."

    return snippet
