#!/usr/bin/env python3
"""
Run GEOSearch against queries extracted from a benchmark TSV.

Usage:
    python scripts/run_benchmark_tsv.py [path/to/benchmark.tsv] [--out results.tsv]

Reads unique (query_id, query) pairs from the TSV and calls the hybrid search
API for each, writing ranked accessions and scores to stdout (or --out file).

Output columns:
    query_id, query, rank, accession, score
"""
import csv
import sys
import time
from pathlib import Path

# Allow running from repo root or scripts/
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.session import SessionLocal
from search.hybrid_search import HybridSearchEngine


def load_unique_queries(tsv_path: Path) -> list[tuple[str, str]]:
    """Return ordered unique (query_id, query) pairs from the benchmark TSV."""
    seen: dict[str, str] = {}  # query_id -> query (preserves first-seen order)
    with open(tsv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            qid = row["query_id"].strip()
            q = row["query"].strip()
            if qid not in seen:
                seen[qid] = q
    return list(seen.items())


def run_queries(queries: list[tuple[str, str]], writer: csv.writer) -> None:
    db = SessionLocal()
    try:
        engine = HybridSearchEngine(db)
        for query_id, query in queries:
            print(f"  [{query_id}] {query!r} ...", end=" ", flush=True)
            t0 = time.time()
            result = engine.search(query=query)
            elapsed = time.time() - t0
            results = result["results"]
            print(f"{len(results)} results in {elapsed:.2f}s")
            for rank, item in enumerate(results, start=1):
                writer.writerow([query_id, query, rank, item["accession"], f"{item.get('score', ''):.4f}" if item.get("score") else ""])
    finally:
        db.close()


def main() -> None:
    args = sys.argv[1:]

    # Parse arguments
    tsv_path = Path("benchmarks-1/benchmark.tsv")
    out_path = None

    i = 0
    while i < len(args):
        if args[i] == "--out" and i + 1 < len(args):
            out_path = Path(args[i + 1])
            i += 2
        else:
            tsv_path = Path(args[i])
            i += 1

    if not tsv_path.exists():
        print(f"ERROR: TSV not found: {tsv_path}", file=sys.stderr)
        sys.exit(1)

    queries = load_unique_queries(tsv_path)
    print(f"Loaded {len(queries)} unique queries from {tsv_path}")
    for qid, q in queries:
        print(f"  {qid}: {q!r}")
    print()

    header = ["query_id", "query", "rank", "accession", "score"]

    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh, delimiter="\t")
            writer.writerow(header)
            run_queries(queries, writer)
        print(f"\nResults written to {out_path}")
    else:
        writer = csv.writer(sys.stdout, delimiter="\t")
        writer.writerow(header)
        run_queries(queries, writer)


if __name__ == "__main__":
    main()
