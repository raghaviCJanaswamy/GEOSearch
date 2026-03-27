#!/usr/bin/env python3
"""
Performance benchmark script for GEOSearch system.
Tests search performance across different queries and configurations.
"""
import sys
import time
from pathlib import Path
from statistics import mean, median, stdev
from typing import Any

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db import get_db, GSESeries, MeshTerm, GSEMesh
from mesh.query_expand import QueryExpander
from search.hybrid_search import HybridSearchEngine


# Test queries covering different topics
TEST_QUERIES = [
    # Medical conditions
    "breast cancer",
    "lung cancer",
    "heart attack",
    "myocardial infarction",
    "alzheimer disease",
    "diabetes mellitus",
    "diabetes type 2",

    # Techniques
    "RNA sequencing",
    "single cell RNA-seq",
    "microarray gene expression",
    "ChIP-seq histone modification",

    # Biological processes
    "immune response",
    "cell differentiation",
    "apoptosis programmed cell death",
    "gene regulation transcription",

    # Specific genes/proteins
    "BRCA1 breast cancer",
    "p53 tumor suppressor",
    "insulin signaling pathway",

    # Complex queries
    "breast cancer estrogen receptor positive",
    "type 2 diabetes insulin resistance",
    "lung cancer smoking related gene expression",
]


def benchmark_database_stats():
    """Collect database statistics."""
    print("\n" + "=" * 80)
    print("DATABASE STATISTICS")
    print("=" * 80)

    db = next(get_db())

    stats = {}

    # GSE records
    stats['total_gse'] = db.query(GSESeries).count()

    # Count by organism
    from sqlalchemy import func, String
    organism_counts = db.query(
        func.jsonb_array_elements_text(GSESeries.organisms).label('organism'),
        func.count().label('count')
    ).group_by('organism').all()
    stats['organisms'] = {org: count for org, count in organism_counts}

    # Count by tech type
    tech_counts = db.query(
        GSESeries.tech_type,
        func.count()
    ).group_by(GSESeries.tech_type).all()
    stats['tech_types'] = {tech or 'unknown': count for tech, count in tech_counts}

    # MeSH terms
    stats['total_mesh_terms'] = db.query(MeshTerm).count()

    # MeSH associations
    stats['total_associations'] = db.query(GSEMesh).count()
    stats['gse_with_mesh'] = db.query(GSEMesh.accession).distinct().count()

    # Average associations per dataset
    if stats['gse_with_mesh'] > 0:
        stats['avg_associations'] = stats['total_associations'] / stats['gse_with_mesh']
    else:
        stats['avg_associations'] = 0

    db.close()

    # Print stats
    print(f"\nGEO Datasets:")
    print(f"  Total: {stats['total_gse']}")
    print(f"\nBy Organism:")
    for org, count in sorted(stats['organisms'].items(), key=lambda x: x[1], reverse=True)[:5]:
        print(f"  {org}: {count}")
    print(f"\nBy Technology:")
    for tech, count in sorted(stats['tech_types'].items(), key=lambda x: x[1], reverse=True):
        print(f"  {tech}: {count}")
    print(f"\nMeSH Integration:")
    print(f"  Total MeSH terms: {stats['total_mesh_terms']:,}")
    print(f"  Total associations: {stats['total_associations']:,}")
    print(f"  Datasets with MeSH: {stats['gse_with_mesh']}/{stats['total_gse']} ({100*stats['gse_with_mesh']/stats['total_gse']:.1f}%)")
    print(f"  Avg associations/dataset: {stats['avg_associations']:.1f}")

    return stats


def benchmark_mesh_expansion():
    """Benchmark MeSH query expansion performance."""
    print("\n" + "=" * 80)
    print("MESH QUERY EXPANSION PERFORMANCE")
    print("=" * 80)

    db = next(get_db())
    expander = QueryExpander(db)

    results = []

    for query in TEST_QUERIES:
        start = time.time()
        expansion = expander.expand_query(query)
        elapsed = (time.time() - start) * 1000  # ms

        results.append({
            'query': query,
            'elapsed_ms': elapsed,
            'matched_terms': len(expansion['matched_terms']),
            'expanded_length': len(expansion['expanded_query'])
        })

    db.close()

    # Print results
    print(f"\nTested {len(TEST_QUERIES)} queries:\n")
    print(f"{'Query':<45} {'Time (ms)':<12} {'Terms':<8} {'Expanded Len':<15}")
    print("-" * 80)

    for r in results[:10]:  # Show first 10
        print(f"{r['query']:<45} {r['elapsed_ms']:>10.1f}  {r['matched_terms']:>6}  {r['expanded_length']:>13}")

    if len(results) > 10:
        print(f"... and {len(results) - 10} more")

    # Statistics
    times = [r['elapsed_ms'] for r in results]
    terms = [r['matched_terms'] for r in results]

    print(f"\nSummary:")
    print(f"  Average time: {mean(times):.1f} ms")
    print(f"  Median time: {median(times):.1f} ms")
    print(f"  Std dev: {stdev(times):.1f} ms" if len(times) > 1 else "  Std dev: N/A")
    print(f"  Min/Max time: {min(times):.1f} / {max(times):.1f} ms")
    print(f"  Average terms matched: {mean(terms):.1f}")
    print(f"  Queries with matches: {sum(1 for t in terms if t > 0)}/{len(terms)} ({100*sum(1 for t in terms if t > 0)/len(terms):.1f}%)")

    return results


def benchmark_hybrid_search():
    """Benchmark hybrid search performance."""
    print("\n" + "=" * 80)
    print("HYBRID SEARCH PERFORMANCE")
    print("=" * 80)

    db = next(get_db())
    engine = HybridSearchEngine(db)

    results = []

    for query in TEST_QUERIES:
        # Test with all search modes
        start = time.time()
        search_result = engine.search(
            query=query,
            use_semantic=True,
            use_lexical=True,
            use_mesh=True,
            top_k=50
        )
        elapsed = (time.time() - start) * 1000  # ms

        results.append({
            'query': query,
            'elapsed_ms': elapsed,
            'semantic_count': search_result['metadata']['semantic_count'],
            'lexical_count': search_result['metadata']['lexical_count'],
            'mesh_terms': len(search_result['metadata']['mesh_terms']),
            'total_results': len(search_result['results'])
        })

    db.close()

    # Print results
    print(f"\nTested {len(TEST_QUERIES)} queries with all search modes enabled:\n")
    print(f"{'Query':<40} {'Time (ms)':<12} {'Semantic':<10} {'Lexical':<10} {'MeSH':<6} {'Results':<8}")
    print("-" * 95)

    for r in results[:10]:  # Show first 10
        print(f"{r['query']:<40} {r['elapsed_ms']:>10.1f}  {r['semantic_count']:>8}  {r['lexical_count']:>8}  {r['mesh_terms']:>4}  {r['total_results']:>6}")

    if len(results) > 10:
        print(f"... and {len(results) - 10} more")

    # Statistics
    times = [r['elapsed_ms'] for r in results]

    print(f"\nSummary:")
    print(f"  Average time: {mean(times):.1f} ms")
    print(f"  Median time: {median(times):.1f} ms")
    print(f"  Std dev: {stdev(times):.1f} ms" if len(times) > 1 else "  Std dev: N/A")
    print(f"  Min/Max time: {min(times):.1f} / {max(times):.1f} ms")
    print(f"  Average results: {mean([r['total_results'] for r in results]):.1f}")
    print(f"  Queries with results: {sum(1 for r in results if r['total_results'] > 0)}/{len(results)} ({100*sum(1 for r in results if r['total_results'] > 0)/len(results):.1f}%)")

    return results


def benchmark_search_modes():
    """Compare performance of different search mode combinations."""
    print("\n" + "=" * 80)
    print("SEARCH MODE COMPARISON")
    print("=" * 80)

    db = next(get_db())
    engine = HybridSearchEngine(db)

    # Test different mode combinations
    modes = [
        ("Semantic Only", True, False, False),
        ("Lexical Only", False, True, False),
        ("MeSH Only", False, False, True),
        ("Semantic + Lexical", True, True, False),
        ("Semantic + MeSH", True, False, True),
        ("Lexical + MeSH", False, True, True),
        ("All (Hybrid)", True, True, True),
    ]

    # Use subset of queries
    test_queries = TEST_QUERIES[:5]

    mode_results = {}

    for mode_name, use_sem, use_lex, use_mesh in modes:
        times = []
        result_counts = []

        for query in test_queries:
            start = time.time()
            search_result = engine.search(
                query=query,
                use_semantic=use_sem,
                use_lexical=use_lex,
                use_mesh=use_mesh,
                top_k=50
            )
            elapsed = (time.time() - start) * 1000

            times.append(elapsed)
            result_counts.append(len(search_result['results']))

        mode_results[mode_name] = {
            'avg_time': mean(times),
            'avg_results': mean(result_counts)
        }

    db.close()

    # Print comparison
    print(f"\nTested {len(test_queries)} queries with each mode:\n")
    print(f"{'Mode':<20} {'Avg Time (ms)':<15} {'Avg Results':<15}")
    print("-" * 50)

    for mode_name, stats in mode_results.items():
        print(f"{mode_name:<20} {stats['avg_time']:>13.1f}  {stats['avg_results']:>13.1f}")

    return mode_results


def benchmark_filters():
    """Benchmark search with different filter configurations."""
    print("\n" + "=" * 80)
    print("FILTER PERFORMANCE")
    print("=" * 80)

    db = next(get_db())
    engine = HybridSearchEngine(db)

    query = "gene expression"  # Generic query

    filter_configs = [
        ("No filters", {}),
        ("Organism: Homo sapiens", {"organisms": ["Homo sapiens"]}),
        ("Organism: Mus musculus", {"organisms": ["Mus musculus"]}),
        ("Tech: rna-seq", {"tech_type": "rna-seq"}),
        ("Tech: microarray", {"tech_type": "microarray"}),
        ("Min samples: 10", {"min_samples": 10}),
        ("Multi-filter", {"organisms": ["Homo sapiens"], "min_samples": 5}),
    ]

    results = []

    for filter_name, filters in filter_configs:
        start = time.time()
        search_result = engine.search(
            query=query,
            filters=filters,
            top_k=50
        )
        elapsed = (time.time() - start) * 1000

        results.append({
            'filter': filter_name,
            'elapsed_ms': elapsed,
            'results': len(search_result['results'])
        })

    db.close()

    # Print results
    print(f"\nQuery: '{query}'\n")
    print(f"{'Filter Configuration':<30} {'Time (ms)':<12} {'Results':<10}")
    print("-" * 52)

    for r in results:
        print(f"{r['filter']:<30} {r['elapsed_ms']:>10.1f}  {r['results']:>8}")

    return results


def benchmark_scaling():
    """Test search performance with different result limits."""
    print("\n" + "=" * 80)
    print("SCALABILITY TEST (Result Limit)")
    print("=" * 80)

    db = next(get_db())
    engine = HybridSearchEngine(db)

    query = "cancer"  # Should match many results
    top_k_values = [10, 25, 50, 100, 200]

    results = []

    for top_k in top_k_values:
        start = time.time()
        search_result = engine.search(query=query, top_k=top_k)
        elapsed = (time.time() - start) * 1000

        results.append({
            'top_k': top_k,
            'elapsed_ms': elapsed,
            'results': len(search_result['results'])
        })

    db.close()

    # Print results
    print(f"\nQuery: '{query}'\n")
    print(f"{'Top K':<10} {'Time (ms)':<12} {'Results':<10} {'Time/Result (ms)':<20}")
    print("-" * 52)

    for r in results:
        time_per_result = r['elapsed_ms'] / r['results'] if r['results'] > 0 else 0
        print(f"{r['top_k']:<10} {r['elapsed_ms']:>10.1f}  {r['results']:>8}  {time_per_result:>18.2f}")

    return results


def main():
    """Run all benchmarks."""
    print("=" * 80)
    print("GEOSEARCH PERFORMANCE BENCHMARK")
    print("=" * 80)
    print(f"\nStarting comprehensive performance testing...")

    all_results = {}

    try:
        # 1. Database stats
        all_results['db_stats'] = benchmark_database_stats()

        # 2. MeSH expansion
        all_results['mesh_expansion'] = benchmark_mesh_expansion()

        # 3. Hybrid search
        all_results['hybrid_search'] = benchmark_hybrid_search()

        # 4. Search mode comparison
        all_results['search_modes'] = benchmark_search_modes()

        # 5. Filter performance
        all_results['filters'] = benchmark_filters()

        # 6. Scaling test
        all_results['scaling'] = benchmark_scaling()

        print("\n" + "=" * 80)
        print("BENCHMARK COMPLETE")
        print("=" * 80)
        print(f"\nAll benchmarks completed successfully!")
        print(f"Results include:")
        print(f"  - Database statistics")
        print(f"  - MeSH expansion performance ({len(all_results['mesh_expansion'])} queries)")
        print(f"  - Hybrid search performance ({len(all_results['hybrid_search'])} queries)")
        print(f"  - Search mode comparison")
        print(f"  - Filter performance")
        print(f"  - Scalability testing")

        return all_results

    except Exception as e:
        print(f"\n❌ Benchmark failed: {e}")
        import traceback
        traceback.print_exc()
        return None


if __name__ == "__main__":
    results = main()
    sys.exit(0 if results else 1)
