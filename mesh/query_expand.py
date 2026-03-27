"""
Query expansion using MeSH terms.
Expands user queries with MeSH synonyms and related terms.
"""
import logging
import re
from typing import Any

from sqlalchemy import String, func, or_
from sqlalchemy.orm import Session

from db import MeshTerm, get_db

logger = logging.getLogger(__name__)


class QueryExpander:
    """
    Expands search queries using MeSH terminology.
    """

    def __init__(self, db: Session):
        """
        Initialize query expander.

        Args:
            db: Database session
        """
        self.db = db

    def expand_query(
        self,
        query: str,
        max_terms: int = 5,
        include_synonyms: bool = True,
    ) -> dict[str, Any]:
        """
        Expand a search query using MeSH terms.

        Args:
            query: User's search query
            max_terms: Maximum number of MeSH terms to include
            include_synonyms: Include MeSH entry terms (synonyms)

        Returns:
            Dictionary with:
                - original_query: Original query text
                - expanded_query: Query with MeSH expansions
                - matched_terms: List of matched MeSH terms
                - expansion_tokens: List of expansion text added

        Example:
            >>> expander = QueryExpander(db)
            >>> result = expander.expand_query("breast cancer RNA-seq")
            >>> print(result['expanded_query'])
            'breast cancer RNA-seq breast neoplasms mammary cancer rna sequencing...'
        """
        logger.info(f"Expanding query: '{query}'")

        # Tokenize query
        tokens = self._tokenize(query)

        # Find matching MeSH terms
        matched_terms = self._find_matching_mesh_terms(tokens, max_terms)

        if not matched_terms:
            logger.info("No MeSH terms matched")
            return {
                "original_query": query,
                "expanded_query": query,
                "matched_terms": [],
                "expansion_tokens": [],
            }

        # Build expansion
        expansion_tokens = []

        for term_info in matched_terms:
            # Add preferred name if different from original
            preferred = term_info["preferred_name"]
            if preferred.lower() not in query.lower():
                expansion_tokens.append(preferred)

            # Add selected entry terms (synonyms)
            if include_synonyms and term_info.get("entry_terms"):
                # Add up to 2 most relevant entry terms per MeSH term
                for entry_term in term_info["entry_terms"][:2]:
                    if entry_term.lower() not in query.lower():
                        expansion_tokens.append(entry_term)

        # Combine original query with expansions
        expanded_query = f"{query} {' '.join(expansion_tokens)}"

        logger.info(
            f"Expanded query with {len(matched_terms)} MeSH terms, "
            f"{len(expansion_tokens)} expansion tokens"
        )

        return {
            "original_query": query,
            "expanded_query": expanded_query,
            "matched_terms": matched_terms,
            "expansion_tokens": expansion_tokens,
        }

    def _tokenize(self, text: str) -> list[str]:
        """
        Tokenize text into words and phrases.

        Args:
            text: Input text

        Returns:
            List of tokens (words and bigrams/trigrams)
        """
        # Clean and lowercase
        text = text.lower()
        text = re.sub(r'[^\w\s-]', ' ', text)

        # Extract words
        words = text.split()

        # Create n-grams (1, 2, 3)
        tokens = []
        for i in range(len(words)):
            # Unigram
            tokens.append(words[i])

            # Bigram
            if i < len(words) - 1:
                tokens.append(f"{words[i]} {words[i+1]}")

            # Trigram
            if i < len(words) - 2:
                tokens.append(f"{words[i]} {words[i+1]} {words[i+2]}")

        return tokens

    def _find_matching_mesh_terms(
        self,
        tokens: list[str],
        max_terms: int,
    ) -> list[dict[str, Any]]:
        """
        Find MeSH terms matching query tokens.

        Args:
            tokens: Query tokens
            max_terms: Maximum number of terms to return

        Returns:
            List of matched MeSH term info dictionaries
        """
        seen_ids: set[str] = set()
        matches: list[dict[str, Any]] = []

        def _add(term: MeshTerm, priority: int) -> None:
            if term.mesh_id in seen_ids:
                return
            seen_ids.add(term.mesh_id)
            matches.append({
                "mesh_id": term.mesh_id,
                "preferred_name": term.preferred_name,
                "entry_terms": term.entry_terms or [],
                "descriptor_ui": term.descriptor_ui,
                "_priority": priority,
            })

        # Pass 1: exact preferred name match (highest priority)
        for token in tokens:
            if len(token) < 3:
                continue
            results = self.db.query(MeshTerm).filter(
                func.lower(MeshTerm.preferred_name) == token.lower()
            ).all()
            for t in results:
                _add(t, 0)

        # Pass 2: exact entry term match
        for token in tokens:
            if len(token) < 3:
                continue
            results = self.db.query(MeshTerm).filter(
                func.lower(func.cast(MeshTerm.entry_terms, String)).like(
                    f'%"{token.lower()}"%'
                )
            ).limit(max_terms).all()
            for t in results:
                _add(t, 1)

        # Pass 3: preferred name starts with token (longer tokens only)
        for token in tokens:
            if len(token) < 5:
                continue
            results = self.db.query(MeshTerm).filter(
                func.lower(MeshTerm.preferred_name).like(f"{token.lower()}%")
            ).limit(max_terms).all()
            for t in results:
                _add(t, 2)

        # Pass 4: partial preferred name match (fallback)
        for token in tokens:
            if len(token) < 5:
                continue
            results = self.db.query(MeshTerm).filter(
                func.lower(MeshTerm.preferred_name).like(f"%{token.lower()}%")
            ).limit(max_terms).all()
            for t in results:
                _add(t, 3)

        # Sort by priority then return top max_terms
        matches.sort(key=lambda x: x["_priority"])
        for m in matches:
            m.pop("_priority", None)
        return matches[:max_terms]


def expand_query_simple(query: str, db: Session | None = None) -> str:
    """
    Simple function to expand a query.

    Args:
        query: Search query
        db: Optional database session (creates one if not provided)

    Returns:
        Expanded query string
    """
    if db is None:
        db_gen = get_db()
        db = next(db_gen)
        close_db = True
    else:
        close_db = False

    try:
        expander = QueryExpander(db)
        result = expander.expand_query(query)
        return result["expanded_query"]
    finally:
        if close_db:
            db.close()
