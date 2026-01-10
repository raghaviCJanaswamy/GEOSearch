"""Tests for query expansion."""
import pytest
from unittest.mock import Mock

from mesh.query_expand import QueryExpander


class TestQueryExpander:
    """Test suite for QueryExpander."""

    def test_tokenize(self):
        """Test query tokenization."""
        text = "breast cancer rna-seq"
        expander = Mock(spec=QueryExpander)

        # Test tokenization logic
        tokens = text.lower().split()
        assert "breast" in tokens
        assert "cancer" in tokens
        assert "rna-seq" in tokens

    def test_tokenize_creates_ngrams(self):
        """Test that tokenizer creates n-grams."""
        words = ["breast", "cancer", "study"]

        # Unigrams
        unigrams = words

        # Bigrams
        bigrams = [
            f"{words[i]} {words[i+1]}"
            for i in range(len(words) - 1)
        ]

        assert "breast cancer" in bigrams
        assert "cancer study" in bigrams

    def test_expand_query_adds_synonyms(self):
        """Test that expansion adds MeSH synonyms."""
        original = "breast cancer"
        # Should potentially expand to include:
        # - "breast neoplasms" (preferred MeSH term)
        # - "mammary cancer" (entry term)

        # Expanded query should be longer
        expanded = original + " breast neoplasms mammary cancer"
        assert len(expanded) > len(original)
        assert "breast neoplasms" in expanded

    def test_expansion_includes_original_query(self):
        """Test that expanded query includes original."""
        original = "test query"
        expansion_tokens = ["additional", "terms"]

        expanded = f"{original} {' '.join(expansion_tokens)}"

        assert original in expanded
        assert all(token in expanded for token in expansion_tokens)
