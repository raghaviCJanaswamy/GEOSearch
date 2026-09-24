#!/usr/bin/env python3
"""
Invoke the GEOSearch hybrid search API for a single query and print results.
Writes query + ranked accessions to benchmarks-1/results.json.

Usage:
    python scripts/search_query.py
    python scripts/search_query.py "pancreatic cancer scRNA-seq"
    python scripts/search_query.py "vitiligo" --no-semantic
    python scripts/search_query.py "vitiligo" --top 20
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
logging.basicConfig(level=logging.WARNING)  # suppress INFO noise from the engine

from db.session import SessionLocal
from search.hybrid_search import HybridSearchEngine

# ── default query ──────────────────────────────────────────────────────────────
DEFAULT_QUERY = "scRNA-seq vitiligo"


def parse_args() -> tuple[str, int, bool, bool, bool]:
    args = sys.argv[1:]
    query = DEFAULT_QUERY
    top = 0          # 0 = print all
    use_semantic = True
    use_lexical  = True
    use_mesh     = True

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--top" and i + 1 < len(args):
            top = int(args[i + 1]); i += 2
        elif a == "--no-semantic":
            use_semantic = False; i += 1
        elif a == "--no-lexical":
            use_lexical = False; i += 1
        elif a == "--no-mesh":
            use_mesh = False; i += 1
        elif not a.startswith("--"):
            query = a; i += 1
        else:
            print(f"Unknown flag: {a}", file=sys.stderr); i += 1

    return query, top, use_semantic, use_lexical, use_mesh


def main() -> None:
    query, top, use_semantic, use_lexical, use_mesh = parse_args()

    print(f"Query       : {query!r}")
    print(f"Modes       : semantic={use_semantic}  lexical={use_lexical}  mesh={use_mesh}")
    print()

    db = SessionLocal()
    try:
        engine = HybridSearchEngine(db)

        t0 = time.time()
        response = engine.search(
            query=query,
            use_semantic=use_semantic,
            use_lexical=use_lexical,
            use_mesh=use_mesh,
            top_k=top if top else None,
        )
        elapsed = time.time() - t0

        results  = response["results"]
        metadata = response["metadata"]

        # ── metadata summary ───────────────────────────────────────────────────
        print("=== Search metadata ===")
        print(f"  Expanded query  : {metadata['expanded_query']!r}")
        mesh_names = [t['preferred_name'] for t in metadata['mesh_terms']]
        print(f"  MeSH terms      : {', '.join(mesh_names) if mesh_names else '(none)'}")
        print(f"  Semantic hits   : {metadata['semantic_count']}")
        print(f"  Lexical hits    : {metadata['lexical_count']}")
        print(f"  MeSH-only hits  : {metadata['mesh_count']}")
        print(f"  Total returned  : {metadata['total_results']}")
        print(f"  Elapsed         : {elapsed:.2f}s")
        print()

        # ── ranked results ─────────────────────────────────────────────────────
        display = results
        print(f"=== Results (showing {len(display)} of {len(results)}) ===")
        print(f"{'Rank':<6} {'Accession':<12} {'Released':<12} {'Title'}")
        print("-" * 90)
        for rank, item in enumerate(display, start=1):
            title    = (item.get("title") or "")[:60]
            released = item.get("submission_date") or item.get("released") or ""
            print(f"{rank:<6} {item['accession']:<12} {str(released):<12} {title}")

            mesh_hits = item.get("matched_mesh_terms", [])
            if mesh_hits:
                names = ", ".join(t["preferred_name"] for t in mesh_hits)
                print(f"       MeSH: {names}")

        # ── write results.json ─────────────────────────────────────────────────
        out_path = Path(__file__).parent.parent / "benchmarks-1" / "results.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        output = {
            "query": query,
            "total_results": len(results),
            "accessions": [item["accession"] for item in results],
        }
        out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
        print(f"\nResults written to {out_path}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
