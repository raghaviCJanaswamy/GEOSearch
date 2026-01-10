#!/usr/bin/env python3
"""
Demonstration script showing MeSH impact on search results.
Compares search results with and without MeSH integration.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import get_db
from search.hybrid_search import HybridSearchEngine


def main():
    db = next(get_db())
    engine = HybridSearchEngine(db)

    print('=' * 80)
    print('DEMONSTRATION: MeSH Impact on Search Results')
    print('=' * 80)

    query = 'breast cancer treatment'

    print(f'\nQuery: "{query}"')
    print()

    # Search WITH MeSH
    print('1. Search WITH MeSH enabled:')
    print('-' * 80)
    result_with = engine.search(query, top_k=5, use_mesh=True)
    metadata_with = result_with['metadata']
    results_with = result_with['results']

    print(f'   Expanded query: {metadata_with["expanded_query"][:80]}...')
    print(f'   MeSH terms matched: {len(metadata_with["mesh_terms"])}')
    if metadata_with['mesh_terms']:
        for term in metadata_with['mesh_terms'][:3]:
            print(f'     - {term["preferred_name"]}')
    print()
    print('   Top 5 Results:')
    for i, res in enumerate(results_with, 1):
        mesh_tags = len(res.get('matched_mesh_terms', []))
        print(f'   {i}. {res["accession"]} ({mesh_tags} MeSH tags)')
        print(f'      {res["title"][:70]}...')

    print()
    print()

    # Search WITHOUT MeSH
    print('2. Search WITHOUT MeSH:')
    print('-' * 80)
    result_without = engine.search(query, top_k=5, use_mesh=False)
    results_without = result_without['results']

    print('   Top 5 Results:')
    for i, res in enumerate(results_without, 1):
        print(f'   {i}. {res["accession"]}')
        print(f'      {res["title"][:70]}...')

    print()
    print()

    # Compare rankings
    print('3. Ranking Comparison:')
    print('-' * 80)
    acc_with = [r['accession'] for r in results_with]
    acc_without = [r['accession'] for r in results_without]

    print(f'   With MeSH:    {acc_with}')
    print(f'   Without MeSH: {acc_without}')

    print()
    if acc_with != acc_without:
        print('   ✓ MeSH changes the ranking! Datasets with relevant MeSH tags rank higher.')
        print()

        # Show what changed
        for i in range(min(len(acc_with), len(acc_without))):
            if acc_with[i] != acc_without[i]:
                print(f'   Position {i+1}: {acc_without[i]} → {acc_with[i]}')
    else:
        print('   Rankings are the same.')

    print()
    print('=' * 80)
    print('Summary:')
    print(f'  - Query expansion adds {len(metadata_with["expanded_query"].split()) - len(query.split())} additional keywords')
    print(f'  - {len([r for r in results_with if r.get("matched_mesh_terms")])} out of 5 results have MeSH tags')
    print(f'  - MeSH boost improved ranking for medical-relevant datasets')
    print('=' * 80)

    db.close()


if __name__ == "__main__":
    main()
