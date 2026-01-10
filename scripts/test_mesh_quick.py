#!/usr/bin/env python3
"""Quick test to verify MeSH integration is working."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import get_db
from search.hybrid_search import HybridSearchEngine

print("\n" + "=" * 70)
print("Quick MeSH Integration Test")
print("=" * 70)

db = next(get_db())

# Test 1: Query expansion
print("\n1. Testing Query Expansion...")
from mesh.query_expand import QueryExpander
expander = QueryExpander(db)
result = expander.expand_query("breast cancer")

if result['matched_terms']:
    print(f"   ✓ Query expansion working")
    print(f"   Original: '{result['original_query']}'")
    print(f"   Expanded: '{result['expanded_query'][:80]}...'")
    print(f"   Matched {len(result['matched_terms'])} MeSH terms")
else:
    print(f"   ✗ No MeSH terms matched")

# Test 2: Search with MeSH
print("\n2. Testing Hybrid Search with MeSH...")
engine = HybridSearchEngine(db)
search_result = engine.search("breast cancer", top_k=3)

mesh_count = len(search_result['metadata'].get('mesh_terms', []))
results_with_mesh = sum(1 for r in search_result['results'] if r.get('matched_mesh_terms'))

if mesh_count > 0:
    print(f"   ✓ MeSH search working")
    print(f"   {mesh_count} MeSH terms used in search")
    print(f"   {results_with_mesh}/{len(search_result['results'])} results have MeSH tags")
else:
    print(f"   ✗ MeSH search not working")

# Test 3: Show top result
if search_result['results']:
    print("\n3. Top Result:")
    top = search_result['results'][0]
    print(f"   {top['accession']}")
    print(f"   {top['title'][:65]}...")
    mesh_terms = top.get('matched_mesh_terms', [])
    if mesh_terms:
        print(f"   MeSH tags: {', '.join([t.get('term', 'N/A') for t in mesh_terms[:3]])}")

print("\n" + "=" * 70)
print("✓ MeSH integration test complete!")
print("=" * 70 + "\n")

db.close()
