#!/usr/bin/env python3
"""
Script to validate MeSH term integration:
1. Associate existing GSE records with MeSH terms
2. Test query expansion
3. Validate MeSH search functionality
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import get_db
from db.models import GSESeries, GSEMesh, MeshTerm
from mesh.matcher import MeSHMatcher
from mesh.query_expand import QueryExpander
from search.hybrid_search import HybridSearchEngine


def associate_mesh_terms():
    """Associate MeSH terms with existing GSE records."""
    db = next(get_db())

    print("=" * 80)
    print("STEP 1: Associating MeSH Terms with GSE Records")
    print("=" * 80)

    # Get all GSE records
    gse_records = db.query(GSESeries).all()
    print(f"Found {len(gse_records)} GSE records")

    if not gse_records:
        print("ERROR: No GSE records found. Run ingestion first.")
        db.close()
        return False

    # Initialize matcher
    matcher = MeSHMatcher(db)

    total_associations = 0
    for gse in gse_records:
        print(f"\nMatching {gse.accession}: {gse.title[:60]}...")

        # Match MeSH terms
        matches = matcher.match_gse(gse.accession, confidence_threshold=0.3)

        if matches:
            print(f"  Found {len(matches)} MeSH term matches")
            for match in matches[:3]:  # Show first 3
                # Look up term name
                mesh_term = db.query(MeshTerm).filter(MeshTerm.mesh_id == match['mesh_id']).first()
                term_name = mesh_term.preferred_name if mesh_term else match['mesh_id']
                print(f"    - {match['mesh_id']}: {term_name} (confidence: {match['confidence']:.2f})")

            # Store associations
            for match in matches:
                assoc = GSEMesh(
                    accession=gse.accession,
                    mesh_id=match['mesh_id'],
                    source='auto',
                    confidence=match['confidence']
                )
                db.merge(assoc)
            total_associations += len(matches)
        else:
            print(f"  No MeSH matches found")

    db.commit()
    db.close()

    print(f"\n✓ Created {total_associations} GSE-MeSH associations")
    return True


def test_query_expansion():
    """Test MeSH query expansion."""
    db = next(get_db())

    print("\n" + "=" * 80)
    print("STEP 2: Testing MeSH Query Expansion")
    print("=" * 80)

    expander = QueryExpander(db)

    test_queries = [
        "breast cancer",
        "lung cancer",
        "RNA sequencing",
        "diabetes",
    ]

    for query in test_queries:
        print(f"\nQuery: '{query}'")
        result = expander.expand_query(query)

        if result['matched_terms']:
            print(f"  Matched MeSH terms ({len(result['matched_terms'])}):")
            for term in result['matched_terms'][:3]:  # Show first 3
                print(f"    - {term['mesh_id']}: {term['preferred_name']}")
            print(f"  Expanded query: {result['expanded_query'][:100]}...")
        else:
            print(f"  No MeSH terms matched")

    db.close()


def test_mesh_search():
    """Test hybrid search with MeSH terms."""
    db = next(get_db())

    print("\n" + "=" * 80)
    print("STEP 3: Testing Hybrid Search with MeSH")
    print("=" * 80)

    engine = HybridSearchEngine(db)

    test_query = "breast cancer"
    print(f"\nSearch query: '{test_query}'")

    result = engine.search(
        query=test_query,
        top_k=5,
        use_semantic=True,
        use_lexical=True,
        use_mesh=True,
    )

    results = result['results']
    metadata = result['metadata']

    print(f"\nSearch Results:")
    print(f"  Semantic matches: {metadata.get('semantic_count', 0)}")
    print(f"  Lexical matches: {metadata.get('lexical_count', 0)}")
    print(f"  MeSH matches: {metadata.get('mesh_count', 0)}")
    print(f"  Total results: {len(results)}")

    if metadata.get('mesh_count', 0) > 0:
        print("\n✓ MeSH search is working!")
    else:
        print("\n⚠ MeSH search returned 0 results. Check:")
        print("  1. Are MeSH terms associated with GSE records?")
        print("  2. Does the query match any MeSH terms?")

    print("\nTop 3 results:")
    for i, res in enumerate(results[:3], 1):
        print(f"\n{i}. {res['accession']}")
        print(f"   Title: {res['title'][:70]}...")

        # Show matched MeSH terms if any
        mesh_terms = res.get('matched_mesh_terms', [])
        if mesh_terms:
            term_names = [t.get('preferred_name') or t.get('term', 'Unknown') for t in mesh_terms[:3]]
            print(f"   MeSH terms: {', '.join(term_names)}")

    db.close()


def show_statistics():
    """Show final statistics."""
    db = next(get_db())

    print("\n" + "=" * 80)
    print("FINAL STATISTICS")
    print("=" * 80)

    # Count GSE records
    gse_count = db.query(GSESeries).count()

    # Count MeSH terms
    mesh_count = db.query(MeshTerm).count()

    # Count associations
    assoc_count = db.query(GSEMesh).count()

    # GSE records with MeSH terms
    gse_with_mesh = db.query(GSESeries.accession).join(GSEMesh).distinct().count()

    print(f"GSE records: {gse_count}")
    print(f"MeSH terms: {mesh_count}")
    print(f"GSE-MeSH associations: {assoc_count}")
    print(f"GSE records with MeSH tags: {gse_with_mesh}/{gse_count} ({100*gse_with_mesh/gse_count if gse_count > 0 else 0:.1f}%)")

    db.close()


def main():
    print("\nMeSH Term Validation Script")
    print("=" * 80)

    # Step 1: Associate MeSH terms
    if not associate_mesh_terms():
        return 1

    # Step 2: Test query expansion
    test_query_expansion()

    # Step 3: Test MeSH search
    test_mesh_search()

    # Show final stats
    show_statistics()

    print("\n" + "=" * 80)
    print("✓ MeSH validation complete!")
    print("=" * 80)

    return 0


if __name__ == "__main__":
    sys.exit(main())
