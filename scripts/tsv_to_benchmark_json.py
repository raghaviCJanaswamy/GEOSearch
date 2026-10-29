#!/usr/bin/env python3
"""
Convert a benchmark TSV into the benchmark.json format.

Output format (matches benchmarks-1/benchmark.json):
  [
    {
      "query_id": "1",
      "query": "vitiligo",
      "results": [
        {
          "rank": 1,
          "accession": "GSE109563",
          "call": "relevant",
          "confidence": "high",
          "source": "entrez",
          "tier": "1",
          "released": "8/7/26"
        },
        ...
      ]
    },
    ...
  ]

Usage:
    python scripts/tsv_to_benchmark_json.py
    python scripts/tsv_to_benchmark_json.py benchmarks-detailed/LLM_benchmark_100qs.tsv
    python scripts/tsv_to_benchmark_json.py benchmarks-detailed/LLM_benchmark_100qs.tsv --out benchmarks-detailed/benchmark_100qs.json
"""
import csv
import json
import sys
from pathlib import Path

DEFAULT_TSV = Path(__file__).parent.parent / "benchmarks-detailed" / "LLM_benchmark_100qs.tsv"


def parse_args() -> tuple[Path, Path]:
    args = sys.argv[1:]
    tsv_path = DEFAULT_TSV
    out_path = None

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--out" and i + 1 < len(args):
            out_path = Path(args[i + 1]); i += 2
        elif not a.startswith("--"):
            tsv_path = Path(a); i += 1
        else:
            print(f"Unknown flag: {a}", file=sys.stderr); i += 1

    if not tsv_path.exists():
        print(f"ERROR: TSV not found: {tsv_path}", file=sys.stderr)
        sys.exit(1)

    if out_path is None:
        out_path = tsv_path.with_suffix(".json")

    return tsv_path, out_path


def main() -> None:
    tsv_path, out_path = parse_args()

    # Read TSV — preserve insertion order per query_id
    queries: dict[str, dict] = {}  # query_id -> { query_id, query, results: [] }

    with open(tsv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            qid   = row["query_id"].strip()
            query = row["query"].strip()

            if qid not in queries:
                queries[qid] = {
                    "query_id": qid,
                    "query":    query,
                    "results":  [],
                }

            # Build result entry from all columns except query_id and query
            result: dict = {}
            for col in reader.fieldnames:
                if col in ("query_id", "query"):
                    continue
                val = row[col].strip()
                # Cast rank and tier to int where possible
                if col in ("rank", "tier"):
                    try:
                        val = int(val)
                    except ValueError:
                        pass
                result[col] = val

            queries[qid]["results"].append(result)

    output = list(queries.values())

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")

    total_results = sum(len(q["results"]) for q in output)
    print(f"Queries    : {len(output)}")
    print(f"Results    : {total_results}")
    print(f"Output     : {out_path}")


if __name__ == "__main__":
    main()
