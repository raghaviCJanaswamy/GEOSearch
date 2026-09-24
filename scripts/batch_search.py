#!/usr/bin/env python3
"""
Run multiple search queries and export results to benchmarks-1/batch-output.json.

Input sources (can be combined):
    --json   benchmarks-1/benchmark.json   Extract unique "query" values from a
                                            benchmark JSON (array of query objects)
    --file   queries.txt                   Plain text file, one query per line
    positional args                        Queries typed directly on the command line

Usage:
    # From benchmark JSON  ← primary use case
    python scripts/batch_search.py --json benchmarks-1/benchmark.json

    # From a plain text file
    python scripts/batch_search.py --file queries.txt

    # Inline queries
    python scripts/batch_search.py "scRNA-seq vitiligo" "FOXP3 knockout"

    # Mix sources
    python scripts/batch_search.py --json benchmarks-1/benchmark.json "extra query"

    # Options
    python scripts/batch_search.py --json benchmarks-1/benchmark.json --top 30
    python scripts/batch_search.py --json benchmarks-1/benchmark.json --out-dir benchmarks-2
"""
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
logging.basicConfig(level=logging.WARNING)

from db.session import SessionLocal
from search.hybrid_search import HybridSearchEngine


def slugify(query: str, max_len: int = 50) -> str:
    """Convert a query string to a safe filename slug."""
    s = query.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s]+", "_", s)
    return s[:max_len]


def parse_args() -> tuple[list[str], int, bool, bool, bool, Path]:
    args = sys.argv[1:]
    queries: list[str] = []
    top = 0
    use_semantic = True
    use_lexical  = True
    use_mesh     = True
    out_dir = Path(__file__).parent.parent / "benchmarks-1"

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--json" and i + 1 < len(args):
            jpath = Path(args[i + 1])
            if not jpath.exists():
                print(f"ERROR: JSON file not found: {jpath}", file=sys.stderr)
                sys.exit(1)
            data = json.loads(jpath.read_text(encoding="utf-8"))
            seen: set[str] = set()
            for obj in data:
                q = obj.get("query", "").strip()
                if q and q not in seen:
                    queries.append(q)
                    seen.add(q)
            print(f"Loaded {len(seen)} unique queries from {jpath.name}")
            i += 2
        elif a == "--file" and i + 1 < len(args):
            fpath = Path(args[i + 1])
            if not fpath.exists():
                print(f"ERROR: query file not found: {fpath}", file=sys.stderr)
                sys.exit(1)
            for line in fpath.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    queries.append(line)
            i += 2
        elif a == "--top" and i + 1 < len(args):
            top = int(args[i + 1]); i += 2
        elif a == "--out-dir" and i + 1 < len(args):
            out_dir = Path(args[i + 1]); i += 2
        elif a == "--no-semantic":
            use_semantic = False; i += 1
        elif a == "--no-lexical":
            use_lexical = False; i += 1
        elif a == "--no-mesh":
            use_mesh = False; i += 1
        elif not a.startswith("--"):
            queries.append(a); i += 1
        else:
            print(f"Unknown flag: {a}", file=sys.stderr); i += 1

    if not queries:
        print("ERROR: provide at least one query, or use --file <queries.txt>", file=sys.stderr)
        print(__doc__)
        sys.exit(1)

    return queries, top, use_semantic, use_lexical, use_mesh, out_dir


def run_query(engine: HybridSearchEngine, query: str, top: int,
              use_semantic: bool, use_lexical: bool, use_mesh: bool) -> dict:
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

    return {
        "elapsed": elapsed,
        "output": [
            {
                "query": query,
                "total_results": len(results),
                "expanded_query": metadata.get("expanded_query", ""),
                "mesh_terms": [t["preferred_name"] for t in metadata.get("mesh_terms", [])],
                "results": [
                    {
                        "rank": rank,
                        "accession": r.get("accession", ""),
                        "title": r.get("title", ""),
                        "organisms": r.get("organisms") or [],
                        "tech_type": r.get("tech_type", ""),
                        "sample_count": r.get("sample_count"),
                        "submission_date": (r.get("submission_date") or "")[:10],
                        "summary": r.get("summary", ""),
                        "geo_url": r.get("geo_url", ""),
                        "matched_mesh_terms": [
                            t["preferred_name"] for t in (r.get("matched_mesh_terms") or [])
                        ],
                    }
                    for rank, r in enumerate(results, start=1)
                ],
            }
        ],
    }


def main() -> None:
    queries, top, use_semantic, use_lexical, use_mesh, out_dir = parse_args()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Queries     : {len(queries)}")
    print(f"Modes       : semantic={use_semantic}  lexical={use_lexical}  mesh={use_mesh}")
    print(f"Top K       : {top if top else 'default'}")
    print(f"Output dir  : {out_dir}")
    print()

    db = SessionLocal()
    batch: list[dict] = []

    try:
        engine = HybridSearchEngine(db)

        for idx, query in enumerate(queries, start=1):
            print(f"[{idx}/{len(queries)}] {query!r} ... ", end="", flush=True)
            try:
                result = run_query(engine, query, top, use_semantic, use_lexical, use_mesh)
                elapsed = result["elapsed"]
                output  = result["output"][0]
                total   = output["total_results"]

                # individual file
                slug = slugify(query)
                out_path = out_dir / f"{slug}.json"
                out_path.write_text(json.dumps([output], indent=2), encoding="utf-8")

                batch.append(output)
                print(f"{total} results in {elapsed:.2f}s -> {out_path.name}")

            except Exception as e:
                print(f"FAILED: {e}", file=sys.stderr)

    finally:
        db.close()

    # combined batch output
    batch_path = out_dir / "batch-output.json"
    batch_path.write_text(json.dumps(batch, indent=2), encoding="utf-8")
    print(f"\nBatch file  : {batch_path}  ({len(batch)} queries)")
    print(f"Done. Files written to {out_dir}/")


if __name__ == "__main__":
    main()
