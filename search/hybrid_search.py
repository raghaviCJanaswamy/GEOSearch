"""
Hybrid search combining semantic, lexical, and MeSH-based search.
Implements Reciprocal Rank Fusion (RRF) for result merging.
"""
import logging
import re
from typing import Any

from sqlalchemy import String, and_, func, or_
from sqlalchemy.orm import Session

from config import settings
from db import GSEMesh, GSESeries, MeshTerm, SessionLocal
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
    ) -> dict[str, Any]:
        """
        Perform hybrid search.

        Args:
            query: Search query text
            filters: Structured filters (organisms, tech_type, date_range, min_samples)
            use_semantic: Enable semantic search
            use_lexical: Enable lexical/keyword search
            use_mesh: Enable MeSH expansion

        Returns:
            Dictionary with:
                - results: List of ranked results
                - metadata: Search metadata (query expansion info, etc.)

        Example:
            >>> engine = HybridSearchEngine(db)
            >>> results = engine.search(
            ...     query="breast cancer RNA-seq",
            ...     filters={"organisms": ["Homo sapiens"], "tech_type": "rna-seq"},
            ... )
        """
        filters = filters or {}

        logger.info(
            f"Hybrid search: query='{query}', "
            f"semantic={use_semantic}, lexical={use_lexical}, mesh={use_mesh}"
        )

        # Step 1: MeSH expansion
        expansion_result = None
        expanded_query = query
        matched_mesh_ids = []

        mesh_concept_groups: list[set[str]] = []
        if use_mesh:
            expansion_result = self.query_expander.expand_query(query)
            expanded_query = expansion_result["expanded_query"]
            matched_mesh_ids = [term["mesh_id"] for term in expansion_result["matched_terms"]]
            logger.info(f"MeSH expansion: {len(matched_mesh_ids)} terms matched")

            # Build concept groups: group MeSH IDs by the query token that produced them.
            # Each distinct source token represents a separate concept in the query
            # (e.g. "breast cancer" and "organ transplant" are two different concepts).
            # Used by _mesh_only_search to require intersection across concepts.
            token_to_ids: dict[str, set[str]] = {}
            for term in expansion_result["matched_terms"]:
                src = term.get("source_token", "")
                token_to_ids.setdefault(src, set()).add(term["mesh_id"])
            mesh_concept_groups = list(token_to_ids.values())

        # Step 2: Semantic search
        # Pass original query and MeSH-expanded query separately so the search
        # layer blends them as a weighted average vector (0.7 original + 0.3 expanded).
        # This prevents long MeSH synonym lists from diluting the query intent.
        semantic_results = []
        if use_semantic:
            try:
                _exp = expanded_query if (use_mesh and expanded_query != query) else None
                # Fetch once at the lowest acceptable threshold (0.45); results above
                # higher thresholds naturally rank first because Milvus returns by score.
                # This avoids up to 4 redundant round-trips to Milvus.
                all_semantic = semantic_search(
                    query=query,
                    expanded_query=_exp,
                    top_k=settings.semantic_top_k,
                    min_score=0.45,
                )
                # Apply a tighter threshold when enough high-quality results exist,
                # falling back to lower thresholds progressively.
                for threshold in (0.65, 0.60, 0.50, 0.45):
                    filtered = [r for r in all_semantic if r["score"] >= threshold]
                    if len(filtered) >= 50 or threshold == 0.45:
                        semantic_results = filtered
                        break
                logger.info(f"Semantic search: {len(semantic_results)} results")
            except Exception as e:
                logger.error(f"Semantic search failed: {e}", exc_info=True)
                # Continue without semantic results

        # Step 3: Lexical search — pass original query + matched MeSH terms separately
        # so each is OR'd, not AND'd together
        lexical_results = []
        if use_lexical:
            # Cap MeSH expansion to top 5 most specific terms to prevent over-retrieval.
            # Too many OR'd MeSH synonyms cause broad queries like "lung cancer" to match
            # thousands of loosely related datasets.
            all_mesh_terms = [t["preferred_name"] for t in (expansion_result["matched_terms"] if expansion_result else [])]
            mesh_preferred = all_mesh_terms[:5]
            # Build mesh_id → preferred_name lookup for concept group AND logic
            mesh_id_to_name = {
                t["mesh_id"]: t["preferred_name"]
                for t in (expansion_result["matched_terms"] if expansion_result else [])
            }
            lexical_results = self._lexical_search(
                query=query,
                mesh_terms=mesh_preferred,
                filters=filters,
                mesh_concept_groups=mesh_concept_groups if use_mesh else None,
                mesh_id_to_name=mesh_id_to_name if use_mesh else None,
            )
            logger.info(f"Lexical search: {len(lexical_results)} results")

        # Step 4: MeSH-only retrieval — fetch datasets directly tagged with matched
        # MeSH IDs. This is the primary retrieval path when use_mesh=True and the
        # query term is a precise MeSH descriptor (e.g. "Vitiligo") that may not
        # appear verbatim in dataset text but IS stored in the gse_mesh table.
        # For multi-concept queries, intersection logic is applied across concept groups.
        mesh_only_results = []
        if use_mesh and matched_mesh_ids:
            mesh_only_results = self._mesh_only_search(
                matched_mesh_ids, filters, mesh_concept_groups
            )
            logger.info(f"MeSH-only search: {len(mesh_only_results)} results")

        # Step 5: Combine results using RRF
        combined_results = self._reciprocal_rank_fusion(
            semantic_results=semantic_results,
            lexical_results=lexical_results,
            mesh_only_results=mesh_only_results,
            matched_mesh_ids=matched_mesh_ids,
        )

        # Step 6: Apply filters and fetch full metadata — no cap, return all matches
        final_results = self._fetch_and_filter_results(
            ranked_accessions=combined_results,
            filters=filters,
            matched_mesh_ids=matched_mesh_ids,
        )

        # Prepare metadata
        metadata = {
            "query": query,
            "expanded_query": expanded_query if use_mesh else query,
            "mesh_terms": expansion_result["matched_terms"] if expansion_result else [],
            "semantic_count": len(semantic_results),
            "lexical_count": len(lexical_results),
            "mesh_count": len(mesh_only_results),
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
        mesh_terms: list[str] | None = None,
        mesh_concept_groups: list[set[str]] | None = None,
        mesh_id_to_name: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Perform lexical search using PostgreSQL full-text search.

        For single-concept queries: original OR logic — MeSH terms are OR'd in
        as additional match pathways alongside the original query tokens.

        For multi-concept queries: AND logic across concept groups — a dataset
        must contain tokens from EACH concept group in its text. This aligns
        with NCBI's behaviour where all query concepts must appear in the record.
        MeSH preferred names are used as the representative token per group.
        """
        tsvec = func.setweight(
            func.to_tsvector("english", func.coalesce(GSESeries.title, "")), "A"
        ).op("||")(
            func.setweight(
                func.to_tsvector("english", func.coalesce(GSESeries.summary, "")), "B"
            )
        ).op("||")(
            func.setweight(
                func.to_tsvector("english", func.coalesce(GSESeries.overall_design, "")), "C"
            )
        )

        # Build tsquery strategy:
        # 1. Original query as a whole phrase AND (plainto_tsquery) — most precise
        # 2. Per-word prefix tsquery AND'd together — catches stemming variants:
        #    "pancreatic cancer" query matches records saying "pancreas cancer"
        #    because to_tsquery('pancrea:*') matches both pancreas and pancreatic
        # 3. Each MeSH preferred name OR'd in as an additional match pathway
        # Result: (phrase OR prefix_word1 & prefix_word2 OR MeSH1 OR MeSH2 ...)
        original_tsquery = func.plainto_tsquery("english", query)

        # Build per-word prefix AND query (catches stemming variants like pancreas/pancreatic)
        query_words = [w.strip("\"'(),.") for w in query.split() if len(w.strip("\"'(),.")) >= 3]
        if len(query_words) > 1:
            # Use prefix matching: 'pancreatic' → 'pancrea:*' catches pancreas/pancreatic
            prefix_parts = " & ".join(f"{w[:6]}:*" for w in query_words)
            prefix_tsquery = func.to_tsquery("english", prefix_parts)
            combined_tsquery = original_tsquery.op("||")(prefix_tsquery)
        else:
            combined_tsquery = original_tsquery

        # Cancer-domain synonym expansion: if query contains a cancer term, also match
        # records using alternative oncology vocabulary (tumor, neoplasm, carcinoma, PDAC etc.)
        # that NCBI catches via PubMed MeSH linkage but we don't have sample-level metadata for.
        CANCER_SYNONYMS = {"cancer", "tumor", "tumour", "neoplas", "carcinoma", "malignancy", "adenocarcinoma"}
        query_lower = query.lower()
        has_cancer_term = any(syn in query_lower for syn in CANCER_SYNONYMS)
        has_organ_term = any(
            w for w in query_words
            if w.lower() not in CANCER_SYNONYMS and len(w) >= 4
        )
        if has_cancer_term and has_organ_term:
            organ_words = [w for w in query_words if w.lower() not in CANCER_SYNONYMS and len(w) >= 4]
            # Use up to 8 chars for organ prefix to reduce false matches (e.g. pancrea:* → pancreatic/pancreas only)
            organ_prefix = " & ".join(f"{w[:8]}:*" for w in organ_words)
            cancer_variants = "cancer:* | tumor:* | neoplas:* | carcinoma:* | adenocarcinoma:* | malign:*"
            expanded_parts = f"({organ_prefix}) & ({cancer_variants})"
            try:
                expanded_tsquery = func.to_tsquery("english", expanded_parts)
                combined_tsquery = combined_tsquery.op("||")(expanded_tsquery)
            except Exception:
                pass  # fallback: skip expansion if tsquery syntax fails

        # OR in each MeSH preferred name as a whole phrase.
        # This is critical for lay-term queries like "heart attack" where the original
        # words don't appear in clinical papers — the MeSH terms drive lexical recall.
        for mt in (mesh_terms or []):
            mt_cleaned = mt.strip("\"'(),.")
            if len(mt_cleaned) >= 3:
                combined_tsquery = combined_tsquery.op("||")(
                    func.plainto_tsquery("english", mt_cleaned)
                )
                # Add prefix variant only for long clinical words not already in the query
                # e.g. "Myocardial Infarction" → "myocard:*" catches myocardial/myocardium
                # Skip short anatomy words like "lung", "small" that over-match
                mt_words = mt_cleaned.split()
                query_words_lower = query.lower().split()
                if (len(mt_words) >= 2
                        and len(mt_words[0]) >= 8
                        and mt_words[0].lower() not in query_words_lower):
                    prefix = mt_words[0][:7].lower()
                    try:
                        combined_tsquery = combined_tsquery.op("||")(
                            func.to_tsquery("english", f"{prefix}:*")
                        )
                    except Exception:
                        pass

        ts_rank = func.ts_rank(tsvec, combined_tsquery)
        ts_match = tsvec.op("@@")(combined_tsquery)

        # Multi-concept AND refinement: when multiple distinct concept groups are
        # detected (e.g. "breast cancer" + "organ transplant"), require the dataset
        # text to contain at least one token from EACH group. This mirrors NCBI's
        # AND behaviour and prevents single-concept datasets from flooding results.
        # Each group is represented by its MeSH preferred names OR'd within the group,
        # then all groups are AND'd together as an additional filter condition.
        if mesh_concept_groups and len(mesh_concept_groups) > 1 and mesh_id_to_name:
            group_conditions = []
            for group_ids in mesh_concept_groups:
                group_names = [mesh_id_to_name[mid] for mid in group_ids if mid in mesh_id_to_name]
                if not group_names:
                    continue
                # Build OR tsquery for all names in this concept group
                group_tsquery = None
                for name in group_names:
                    name_cleaned = name.strip("\"'(),.")
                    if len(name_cleaned) < 3:
                        continue
                    tq = func.plainto_tsquery("english", name_cleaned)
                    group_tsquery = tq if group_tsquery is None else group_tsquery.op("||")(tq)
                if group_tsquery is not None:
                    group_conditions.append(tsvec.op("@@")(group_tsquery))

            if len(group_conditions) > 1:
                # AND across all concept groups — dataset must match all groups
                ts_match = and_(ts_match, *group_conditions)

        filter_conditions = self._build_filter_conditions(filters)
        base_filter = and_(ts_match, *filter_conditions) if filter_conditions else ts_match

        results = (
            self.db.query(GSESeries.accession, ts_rank.label("rank"))
            .filter(base_filter)
            .order_by(ts_rank.desc())
            .all()
        )

        return [
            {"accession": accession, "score": float(rank)}
            for accession, rank in results
        ]

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

    def _mesh_only_search(
        self,
        matched_mesh_ids: list[str],
        filters: dict[str, Any],
        mesh_concept_groups: list[set[str]] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Retrieve datasets directly tagged with matched MeSH IDs.

        When mesh_concept_groups is provided (multiple distinct concepts detected,
        e.g. "breast cancer" + "organ transplant"), a dataset must match at least
        one MeSH ID from EACH concept group — intersection logic. This prevents
        broad single-concept retrieval (e.g. all 6,000+ Breast Neoplasm datasets)
        when the query clearly targets a multi-concept intersection.

        For single-concept queries the original union logic applies.
        """
        # Single-concept query: union retrieval — return all datasets matching any MeSH ID.
        if not mesh_concept_groups or len(mesh_concept_groups) <= 1:
            rows = (
                self.db.query(GSEMesh.accession, func.count(GSEMesh.mesh_id).label("match_count"))
                .filter(GSEMesh.mesh_id.in_(matched_mesh_ids))
                .group_by(GSEMesh.accession)
                .order_by(func.count(GSEMesh.mesh_id).desc())
                .all()
            )
            return [{"accession": acc, "score": float(cnt)} for acc, cnt in rows]

        # Multi-concept query: hard intersection — a dataset must have at least one
        # MeSH tag from EVERY concept group. Start from the smallest group and
        # intersect progressively to keep the candidate set tight.
        groups_sorted = sorted(mesh_concept_groups, key=len)

        candidate_rows = (
            self.db.query(GSEMesh.accession)
            .filter(GSEMesh.mesh_id.in_(groups_sorted[0]))
            .distinct()
            .all()
        )
        candidate_accessions = {row.accession for row in candidate_rows}

        for group in groups_sorted[1:]:
            if not candidate_accessions:
                break
            group_rows = (
                self.db.query(GSEMesh.accession)
                .filter(
                    GSEMesh.accession.in_(candidate_accessions),
                    GSEMesh.mesh_id.in_(group),
                )
                .distinct()
                .all()
            )
            candidate_accessions = {row.accession for row in group_rows}

        if not candidate_accessions:
            return []

        # Score by total matched MeSH IDs across all groups within the intersection set
        rows = (
            self.db.query(GSEMesh.accession, func.count(GSEMesh.mesh_id).label("match_count"))
            .filter(
                GSEMesh.accession.in_(candidate_accessions),
                GSEMesh.mesh_id.in_(matched_mesh_ids),
            )
            .group_by(GSEMesh.accession)
            .order_by(func.count(GSEMesh.mesh_id).desc())
            .all()
        )
        return [{"accession": acc, "score": float(cnt)} for acc, cnt in rows]

    def _reciprocal_rank_fusion(
        self,
        semantic_results: list[dict[str, Any]],
        lexical_results: list[dict[str, Any]],
        matched_mesh_ids: list[str],
        mesh_only_results: list[dict[str, Any]] | None = None,
        k: int | None = None,
    ) -> list[str]:
        """
        Combine results using Reciprocal Rank Fusion (RRF).

        RRF score = sum(1 / (k + rank)) across all result lists

        Args:
            semantic_results: Results from semantic search
            lexical_results: Results from lexical search
            matched_mesh_ids: MeSH IDs matched in query expansion
            mesh_only_results: Results from direct MeSH tag lookup
            k: RRF constant (default: from settings)

        Returns:
            List of accessions ranked by RRF score
        """
        if k is None:
            k = settings.rrf_k

        scores: dict[str, float] = {}

        # Weighted RRF: semantic results are given 2× weight relative to lexical
        # and MeSH-only legs. This prevents broad MeSH tag retrieval (which matches
        # every dataset tagged with a disease regardless of query aspect, e.g.
        # "treatment options" vs "genomics") from burying the more precise semantic
        # signal. Datasets found by semantic search that are also in lexical/MeSH
        # still accumulate extra score from those legs on top.
        SEMANTIC_WEIGHT = 2.0
        LEXICAL_WEIGHT = 1.0
        MESH_WEIGHT = 1.0

        # Add semantic results
        for rank, result in enumerate(semantic_results, start=1):
            accession = result["accession"]
            rrf_score = SEMANTIC_WEIGHT / (k + rank)
            scores[accession] = scores.get(accession, 0.0) + rrf_score

        # Add lexical results
        for rank, result in enumerate(lexical_results, start=1):
            accession = result["accession"]
            rrf_score = LEXICAL_WEIGHT / (k + rank)
            scores[accession] = scores.get(accession, 0.0) + rrf_score

        # Add MeSH-only results (direct tag lookup)
        for rank, result in enumerate(mesh_only_results or [], start=1):
            accession = result["accession"]
            rrf_score = MESH_WEIGHT / (k + rank)
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
    ) -> list[dict[str, Any]]:
        """
        Fetch full metadata for ranked results and apply filters.

        Args:
            ranked_accessions: List of accessions in rank order
            filters: Structured filters
            matched_mesh_ids: MeSH IDs for highlighting

        Returns:
            List of result dictionaries with full metadata
        """
        if not ranked_accessions:
            return []

        # Fetch GSE records in one query
        gse_records = (
            self.db.query(GSESeries)
            .filter(GSESeries.accession.in_(ranked_accessions))
            .all()
        )
        gse_lookup = {gse.accession: gse for gse in gse_records}

        # Fetch all MeSH associations for all accessions in one batched query
        # instead of one query per result (avoids N+1)
        mesh_by_accession: dict[str, list[dict]] = {}
        if matched_mesh_ids:
            all_mesh_assocs = (
                self.db.query(GSEMesh, MeshTerm)
                .join(MeshTerm, GSEMesh.mesh_id == MeshTerm.mesh_id)
                .filter(
                    GSEMesh.accession.in_(ranked_accessions),
                    GSEMesh.mesh_id.in_(matched_mesh_ids),
                )
                .all()
            )
            for assoc, term in all_mesh_assocs:
                mesh_by_accession.setdefault(assoc.accession, []).append({
                    "mesh_id": assoc.mesh_id,
                    "preferred_name": term.preferred_name,
                    "confidence": assoc.confidence,
                })

        # Apply filters and format results
        results = []
        for accession in ranked_accessions:
            if accession not in gse_lookup:
                continue

            gse = gse_lookup[accession]

            if not self._passes_filters(gse, filters):
                continue

            result = {
                **gse.to_dict(),
                "matched_mesh_terms": mesh_by_accession.get(accession, []),
                "geo_url": f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={accession}",
            }
            results.append(result)

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
    db: Session | None = None,
) -> dict[str, Any]:
    """
    Convenience function for performing GEO search.

    Args:
        query: Search query
        filters: Optional filters
        db: Optional database session

    Returns:
        Search results dictionary
    """
    if db is None:
        db = SessionLocal()
        close_db = True
    else:
        close_db = False

    try:
        engine = HybridSearchEngine(db)
        return engine.search(query=query, filters=filters)
    finally:
        if close_db:
            db.close()
