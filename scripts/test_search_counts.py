#!/usr/bin/env python3
"""
Test script: reports result counts for each search mode individually.

Usage:
    python scripts/test_search_counts.py "vitiligo"
    python scripts/test_search_counts.py "vitiligo treatment" "breast cancer" "heart attack"
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import get_db
from search.hybrid_search import HybridSearchEngine

QUERIES = sys.argv[1:] or ["vitiligo", "vitiligo treatment", "breast cancer"]

SEP = "=" * 70

db = next(get_db())
engine = HybridSearchEngine(db)

print(f"\n{SEP}")
print("Search Mode Count Report")
print(SEP)

for query in QUERIES:
    print(f"\nQuery: \"{query}\"")
    print("-" * 50)

    # a) Semantic only
    sem = engine.search(query, use_semantic=True, use_lexical=False, use_mesh=False)
    print(f"  a) Semantic search alone   : {sem['metadata']['total_results']} results")

    # b) Keyword/lexical only
    lex = engine.search(query, use_semantic=False, use_lexical=True, use_mesh=False)
    print(f"  b) Keyword search alone    : {lex['metadata']['total_results']} results")

    # c) MeSH expansion only
    mesh = engine.search(query, use_semantic=False, use_lexical=False, use_mesh=True)
    print(f"  c) MeSH expansion alone    : {mesh['metadata']['total_results']} results")

    # d) MeSH terms detected
    mesh_terms = mesh["metadata"]["mesh_terms"]
    if mesh_terms:
        names = [t["preferred_name"] for t in mesh_terms]
        print(f"  d) MeSH terms detected ({len(names)}): {', '.join(names)}")
    else:
        print(f"  d) MeSH terms detected     : none")

print(f"\n{SEP}\n")

db.close()
