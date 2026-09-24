#!/usr/bin/env python3
"""
Compare LLM benchmark judgments against GEOSearch results.

Takes two JSON files as input:
  LLMResult  — benchmark.json format  (array of { query, judgments: [{accession, ...}] })
  GeoResult  — batch-output.json format (array of { query, results:  [{accession, ...}] })

Matches on query string, then compares accession lists.

Output CSV columns:
  query, llm_accession, llm_rank, llm_call,
  geo_accession, geo_rank,
  matched          (yes/no — same accession in both)
  in_llm_only      (yes if accession appears in LLM but not GEO)
  in_geo_only      (yes if accession appears in GEO but not LLM)

Usage:
    python scripts/compare_results.py benchmarks-1/benchmark.json benchmarks-1/batch-output.json
    python scripts/compare_results.py --llm benchmarks-1/benchmark.json --geo benchmarks-1/batch-output.json
    python scripts/compare_results.py --llm benchmark.json --geo batch-output.json --out comparison.csv
"""
import csv
import json
import sys
from pathlib import Path


def parse_args() -> tuple[Path, Path, Path]:
    args = sys.argv[1:]
    llm_path = geo_path = out_path = None

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--llm" and i + 1 < len(args):
            llm_path = Path(args[i + 1]); i += 2
        elif a == "--geo" and i + 1 < len(args):
            geo_path = Path(args[i + 1]); i += 2
        elif a == "--out" and i + 1 < len(args):
            out_path = Path(args[i + 1]); i += 2
        elif not a.startswith("--"):
            if llm_path is None:
                llm_path = Path(a)
            elif geo_path is None:
                geo_path = Path(a)
            i += 1
        else:
            print(f"Unknown flag: {a}", file=sys.stderr); i += 1

    if not llm_path or not geo_path:
        print("ERROR: provide both LLMResult and GeoResult files.", file=sys.stderr)
        print(__doc__)
        sys.exit(1)

    for p in (llm_path, geo_path):
        if not p.exists():
            print(f"ERROR: file not found: {p}", file=sys.stderr)
            sys.exit(1)

    if out_path is None:
        out_path = llm_path.parent / "comparison.csv"

    return llm_path, geo_path, out_path


def load_llm(path: Path) -> dict[str, list[dict]]:
    """Load benchmark.json → { query: [ {accession, rank, call, ...} ] }"""
    data = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, list[dict]] = {}
    for obj in data:
        query = obj.get("query", "").strip()
        judgments = obj.get("judgments", [])
        if query:
            result[query] = judgments
    return result


def load_geo(path: Path) -> dict[str, list[dict]]:
    """Load batch-output.json → { query: [ {accession, rank, ...} ] }"""
    data = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, list[dict]] = {}
    for obj in data:
        query = obj.get("query", "").strip()
        results = obj.get("results", [])
        if query:
            result[query] = results
    return result


def main() -> None:
    llm_path, geo_path, out_path = parse_args()

    llm_data = load_llm(llm_path)
    geo_data = load_geo(geo_path)

    all_queries = sorted(set(llm_data) | set(geo_data))
    print(f"LLM queries  : {len(llm_data)}")
    print(f"GEO queries  : {len(geo_data)}")
    print(f"Total unique : {len(all_queries)}")
    print()

    rows: list[dict] = []

    for query in all_queries:
        llm_items = llm_data.get(query, [])
        geo_items = geo_data.get(query, [])

        # Build lookup dicts keyed by accession
        llm_by_acc = {j["accession"]: j for j in llm_items}
        geo_by_acc = {r["accession"]: r for r in geo_items}

        llm_accessions = list(llm_by_acc.keys())
        geo_accessions = list(geo_by_acc.keys())
        all_accessions = list(dict.fromkeys(llm_accessions + geo_accessions))  # preserve order

        for acc in all_accessions:
            llm_entry = llm_by_acc.get(acc)
            geo_entry = geo_by_acc.get(acc)

            llm_call = llm_entry.get("call", "") if llm_entry else ""

            if llm_entry and geo_entry and llm_call != "not_relevant":
                match = "BothMatch"
            elif llm_entry and not geo_entry and llm_call == "not_relevant":
                match = "CorrectExclusion"
            elif llm_entry and geo_entry and llm_call == "not_relevant":
                match = "IrrelevantInclusion"
            elif llm_entry and not geo_entry and llm_call in ("relevant", "borderline"):
                match = "GeoMiss"
            else:
                match = "GeoOnly"  # in GEO but not in LLM judgments at all

            rows.append({
                "query":        query,
                "accession":    acc,
                "matched":      match,
                "in_llm_only":  "yes" if (llm_entry and not geo_entry) else "no",
                "in_geo_only":  "yes" if (geo_entry and not llm_entry) else "no",
                "llm_rank":     llm_entry.get("rank", "") if llm_entry else "",
                "llm_call":     llm_call,
                "llm_released": llm_entry.get("released", "") if llm_entry else "",
                "geo_rank":     geo_entry.get("rank", "") if geo_entry else "",
                "geo_title":    (geo_entry.get("title") or "")[:80] if geo_entry else "",
            })

        q_rows = [r for r in rows if r["query"] == query]
        from collections import Counter as _C
        q_counts = _C(r["matched"] for r in q_rows)
        print(f"  {query!r}")
        print(f"    LLM: {len(llm_items)}  GEO: {len(geo_items)}  "
              f"BothMatch: {q_counts['BothMatch']}  "
              f"CorrectExclusion: {q_counts['CorrectExclusion']}  "
              f"IrrelevantInclusion: {q_counts['IrrelevantInclusion']}  "
              f"GeoMiss: {q_counts['GeoMiss']}  "
              f"GeoOnly: {q_counts['GeoOnly']}")

    # Write CSV
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "query", "accession", "matched", "in_llm_only", "in_geo_only",
        "llm_rank", "llm_call", "llm_released", "geo_rank", "geo_title",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    from collections import Counter
    counts = Counter(r["matched"] for r in rows)

    # Per-query summary rows
    summary_rows: list[dict] = []
    for query in all_queries:
        llm_items = llm_data.get(query, [])
        geo_items = geo_data.get(query, [])
        q_counts = Counter(r["matched"] for r in rows if r["query"] == query)
        summary_rows.append({
            "query":                query,
            "llm_judgments":        len(llm_items),
            "geo_results":          len(geo_items),
            "BothMatch":            q_counts["BothMatch"],
            "CorrectExclusion":     q_counts["CorrectExclusion"],
            "IrrelevantInclusion":  q_counts["IrrelevantInclusion"],
            "GeoMiss":              q_counts["GeoMiss"],
            "GeoOnly":              q_counts["GeoOnly"],
        })

    # Totals row
    summary_rows.append({
        "query":                "TOTAL",
        "llm_judgments":        sum(r["llm_judgments"] for r in summary_rows),
        "geo_results":          sum(r["geo_results"] for r in summary_rows),
        "BothMatch":            counts["BothMatch"],
        "CorrectExclusion":     counts["CorrectExclusion"],
        "IrrelevantInclusion":  counts["IrrelevantInclusion"],
        "GeoMiss":              counts["GeoMiss"],
        "GeoOnly":              counts["GeoOnly"],
    })

    summary_path = out_path.parent / "summary.csv"
    summary_fields = [
        "query", "llm_judgments", "geo_results",
        "BothMatch", "CorrectExclusion", "IrrelevantInclusion", "GeoMiss", "GeoOnly",
    ]
    with open(summary_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=summary_fields)
        writer.writeheader()
        writer.writerows(summary_rows)

    print()
    print(f"=== Summary ===")
    print(f"  Total rows           : {len(rows)}")
    print(f"  BothMatch            : {counts['BothMatch']}")
    print(f"  CorrectExclusion     : {counts['CorrectExclusion']}")
    print(f"  IrrelevantInclusion  : {counts['IrrelevantInclusion']}")
    print(f"  GeoMiss              : {counts['GeoMiss']}")
    print(f"  GeoOnly              : {counts['GeoOnly']}")
    print(f"\ncomparison.csv : {out_path}")
    print(f"summary.csv    : {summary_path}")


if __name__ == "__main__":
    main()
