#!/usr/bin/env python3
"""
Compare LLM benchmark results against GEOSearch batch results.

LLMResult  — LLM_benchmark_100qs.json  format:
               [ { "query_id", "query", "results": [{"accession","call",...}] } ]

GeoResult  — batch-output.json format:
               [ { "query", "results": [{"accession","rank",...}] } ]

Match key: query string.

Output CSV  (comparison.csv):
    query, accession, matched, llm_rank, llm_call, llm_confidence, llm_released, geo_rank, geo_title

Output CSV  (summary.csv):
    query, llm_results, geo_results, BothMatch, CorrectExclusion, IrrelevantInclusion, GeoMiss, GeoOnly

matched values:
    BothMatch           — accession in both LLM and GEO (LLM call is relevant/borderline)
    CorrectExclusion    — llm_call == not_relevant AND GEO correctly excluded it
    IrrelevantInclusion — llm_call == not_relevant AND GEO returned it anyway
    GeoMiss             — LLM called relevant/borderline but GEO missed it
    GeoOnly             — in GEO results but not in LLM judgment pool

Usage:
    python scripts/compare_detailed.py benchmarks-detailed/LLM_benchmark_100qs.json benchmarks-detailed/batch-output.json
    python scripts/compare_detailed.py --llm benchmarks-detailed/LLM_benchmark_100qs.json --geo benchmarks-detailed/batch-output.json
    python scripts/compare_detailed.py --llm LLM.json --geo batch.json --out-dir benchmarks-detailed
"""
import csv
import json
import sys
from collections import Counter
from pathlib import Path


def parse_args() -> tuple[Path, Path, Path]:
    args = sys.argv[1:]
    llm_path = geo_path = None
    out_dir = Path(__file__).parent.parent / "benchmarks-detailed"

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--llm" and i + 1 < len(args):
            llm_path = Path(args[i + 1]); i += 2
        elif a == "--geo" and i + 1 < len(args):
            geo_path = Path(args[i + 1]); i += 2
        elif a == "--out-dir" and i + 1 < len(args):
            out_dir = Path(args[i + 1]); i += 2
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

    return llm_path, geo_path, out_dir


def load_llm(path: Path) -> dict[str, dict]:
    """{ query -> { "query_id", "results_by_acc": {acc: entry} } }"""
    data = json.loads(path.read_text(encoding="utf-8"))
    result = {}
    for obj in data:
        query = obj.get("query", "").strip()
        if not query:
            continue
        result[query] = {
            "query_id":       obj.get("query_id", ""),
            "results_by_acc": {r["accession"]: r for r in obj.get("results", [])},
        }
    return result


def load_geo(path: Path) -> dict[str, dict]:
    """{ query -> { "results_by_acc": {acc: entry} } }"""
    data = json.loads(path.read_text(encoding="utf-8"))
    result = {}
    for obj in data:
        query = obj.get("query", "").strip()
        if not query:
            continue
        result[query] = {
            "results_by_acc": {r["accession"]: r for r in obj.get("results", [])},
        }
    return result


def classify(llm_entry: dict | None, geo_entry: dict | None) -> str:
    llm_call = (llm_entry.get("call", "") if llm_entry else "").strip()
    if llm_entry and geo_entry and llm_call != "not_relevant":
        return "BothMatch"
    if llm_entry and not geo_entry and llm_call == "not_relevant":
        return "CorrectExclusion"
    if llm_entry and geo_entry and llm_call == "not_relevant":
        return "IrrelevantInclusion"
    if llm_entry and not geo_entry and llm_call in ("relevant", "borderline"):
        return "GeoMiss"
    return "GeoOnly"


def main() -> None:
    llm_path, geo_path, out_dir = parse_args()
    out_dir.mkdir(parents=True, exist_ok=True)

    llm_data = load_llm(llm_path)
    geo_data = load_geo(geo_path)

    all_queries = sorted(set(llm_data) | set(geo_data))
    print(f"LLM queries  : {len(llm_data)}")
    print(f"GEO queries  : {len(geo_data)}")
    print(f"Total unique : {len(all_queries)}")
    print()

    comparison_rows: list[dict] = []
    summary_rows: list[dict] = []

    for query in all_queries:
        llm_by_acc = llm_data.get(query, {}).get("results_by_acc", {})
        geo_by_acc = geo_data.get(query,  {}).get("results_by_acc", {})

        all_accessions = list(dict.fromkeys(list(llm_by_acc) + list(geo_by_acc)))

        for acc in all_accessions:
            llm_entry = llm_by_acc.get(acc)
            geo_entry = geo_by_acc.get(acc)
            match = classify(llm_entry, geo_entry)

            comparison_rows.append({
                "query":           query,
                "accession":       acc,
                "matched":         match,
                "llm_rank":        llm_entry.get("rank", "")        if llm_entry else "",
                "llm_call":        llm_entry.get("call", "")        if llm_entry else "",
                "llm_confidence":  llm_entry.get("confidence", "")  if llm_entry else "",
                "llm_released":    llm_entry.get("released", "")    if llm_entry else "",
                "geo_rank":        geo_entry.get("rank", "")        if geo_entry else "",
                "geo_title":       (geo_entry.get("title") or "")[:80] if geo_entry else "",
            })

        q_counts = Counter(r["matched"] for r in comparison_rows if r["query"] == query)
        summary_rows.append({
            "query":                query,
            "llm_results":          len(llm_by_acc),
            "geo_results":          len(geo_by_acc),
            "BothMatch":            q_counts["BothMatch"],
            "CorrectExclusion":     q_counts["CorrectExclusion"],
            "IrrelevantInclusion":  q_counts["IrrelevantInclusion"],
            "GeoMiss":              q_counts["GeoMiss"],
            "GeoOnly":              q_counts["GeoOnly"],
        })

        print(f"  {query!r}")
        print(f"    LLM: {len(llm_by_acc)}  GEO: {len(geo_by_acc)}  "
              f"BothMatch: {q_counts['BothMatch']}  "
              f"CorrectExclusion: {q_counts['CorrectExclusion']}  "
              f"IrrelevantInclusion: {q_counts['IrrelevantInclusion']}  "
              f"GeoMiss: {q_counts['GeoMiss']}  "
              f"GeoOnly: {q_counts['GeoOnly']}")

    # Totals row
    total_counts = Counter(r["matched"] for r in comparison_rows)
    summary_rows.append({
        "query":                "TOTAL",
        "llm_results":          sum(r["llm_results"] for r in summary_rows),
        "geo_results":          sum(r["geo_results"] for r in summary_rows),
        "BothMatch":            total_counts["BothMatch"],
        "CorrectExclusion":     total_counts["CorrectExclusion"],
        "IrrelevantInclusion":  total_counts["IrrelevantInclusion"],
        "GeoMiss":              total_counts["GeoMiss"],
        "GeoOnly":              total_counts["GeoOnly"],
    })

    # Write comparison.csv
    comp_path = out_dir / "comparison.csv"
    with open(comp_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=[
            "query", "accession", "matched",
            "llm_rank", "llm_call", "llm_confidence", "llm_released",
            "geo_rank", "geo_title",
        ])
        writer.writeheader()
        writer.writerows(comparison_rows)

    # Write summary.csv
    summ_path = out_dir / "summary.csv"
    with open(summ_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=[
            "query", "llm_results", "geo_results",
            "BothMatch", "CorrectExclusion", "IrrelevantInclusion", "GeoMiss", "GeoOnly",
        ])
        writer.writeheader()
        writer.writerows(summary_rows)

    print()
    print(f"=== Summary ===")
    print(f"  Total rows           : {len(comparison_rows)}")
    print(f"  BothMatch            : {total_counts['BothMatch']}")
    print(f"  CorrectExclusion     : {total_counts['CorrectExclusion']}")
    print(f"  IrrelevantInclusion  : {total_counts['IrrelevantInclusion']}")
    print(f"  GeoMiss              : {total_counts['GeoMiss']}")
    print(f"  GeoOnly              : {total_counts['GeoOnly']}")
    print(f"\ncomparison.csv : {comp_path}")
    print(f"summary.csv    : {summ_path}")


if __name__ == "__main__":
    main()
