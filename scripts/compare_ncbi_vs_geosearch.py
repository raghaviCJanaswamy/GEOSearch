#!/usr/bin/env python3
"""
Compare GEOSearch vs NCBI Entrez results against the LLM ground truth.

Produces per-system and head-to-head comparison CSVs suitable for a paper.

Inputs:
    --llm      LLM ground truth  (benchmarks-detailed/LLM_benchmark_100qs.json)
    --geo      GEOSearch batch   (benchmarks-detailed/run-*/batch-output.json)
    --ncbi     NCBI batch        (benchmarks-ncbi/batch-output.json)

Outputs (written to --out-dir):
    comparison_geo.csv    — per-accession classification for GEOSearch
    comparison_ncbi.csv   — per-accession classification for NCBI
    summary_geo.csv       — per-query summary for GEOSearch
    summary_ncbi.csv      — per-query summary for NCBI
    head_to_head.csv      — side-by-side BothMatch/GeoMiss/etc per query

Usage:
    python scripts/compare_ncbi_vs_geosearch.py \\
        --llm  benchmarks-detailed/LLM_benchmark_100qs.json \\
        --geo  benchmarks-detailed/run-11-openai/batch-output.json \\
        --ncbi benchmarks-ncbi/batch-output.json \\
        --out-dir benchmarks-ncbi
"""
import csv
import json
import sys
from collections import Counter
from pathlib import Path


# ── helpers ───────────────────────────────────────────────────────────────────

def load_llm(path: Path) -> dict[str, dict]:
    """{ query -> { query_id, results_by_acc: {acc: entry} } }"""
    data = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for obj in data:
        q = obj.get("query", "").strip()
        if not q:
            continue
        out[q] = {
            "query_id":       obj.get("query_id", ""),
            "results_by_acc": {r["accession"]: r for r in obj.get("results", [])},
        }
    return out


def load_system(path: Path) -> dict[str, dict]:
    """{ query -> { results_by_acc: {acc: entry} } }  — works for both GEO and NCBI batch."""
    data = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for obj in data:
        q = obj.get("query", "").strip()
        if not q:
            continue
        # batch-output may nest results under obj["output"][0] (GEOSearch old format) or obj directly
        results = obj.get("results")
        if results is None and "output" in obj:
            results = obj["output"][0].get("results", [])
        out[q] = {
            "results_by_acc": {r["accession"]: r for r in (results or [])},
        }
    return out


def classify(llm_entry, sys_entry) -> str:
    llm_call = (llm_entry.get("call", "") if llm_entry else "").strip()
    if llm_entry and sys_entry and llm_call != "not_relevant":
        return "BothMatch"
    if llm_entry and not sys_entry and llm_call == "not_relevant":
        return "CorrectExclusion"
    if llm_entry and sys_entry and llm_call == "not_relevant":
        return "IrrelevantInclusion"
    if llm_entry and not sys_entry and llm_call in ("relevant", "borderline"):
        return "GeoMiss"
    return "SystemOnly"


def build_comparison(llm_data: dict, sys_data: dict) -> tuple[list[dict], list[dict]]:
    all_queries = sorted(set(llm_data) | set(sys_data))
    comparison_rows = []
    summary_rows    = []

    for query in all_queries:
        llm_by_acc = llm_data.get(query, {}).get("results_by_acc", {})
        sys_by_acc = sys_data.get(query,  {}).get("results_by_acc", {})
        all_acc    = list(dict.fromkeys(list(llm_by_acc) + list(sys_by_acc)))

        for acc in all_acc:
            le = llm_by_acc.get(acc)
            se = sys_by_acc.get(acc)
            match = classify(le, se)
            comparison_rows.append({
                "query":          query,
                "accession":      acc,
                "matched":        match,
                "llm_rank":       le.get("rank", "")       if le else "",
                "llm_call":       le.get("call", "")       if le else "",
                "llm_confidence": le.get("confidence", "") if le else "",
                "sys_rank":       se.get("rank", "")       if se else "",
                "sys_title":      (se.get("title") or "")[:80] if se else "",
            })

        q_counts = Counter(r["matched"] for r in comparison_rows if r["query"] == query)
        summary_rows.append({
            "query":               query,
            "llm_results":         len(llm_by_acc),
            "sys_results":         len(sys_by_acc),
            "BothMatch":           q_counts["BothMatch"],
            "CorrectExclusion":    q_counts["CorrectExclusion"],
            "IrrelevantInclusion": q_counts["IrrelevantInclusion"],
            "GeoMiss":             q_counts["GeoMiss"],
            "SystemOnly":          q_counts["SystemOnly"],
        })

    total = Counter(r["matched"] for r in comparison_rows)
    summary_rows.append({
        "query":               "TOTAL",
        "llm_results":         sum(r["llm_results"] for r in summary_rows),
        "sys_results":         sum(r["sys_results"] for r in summary_rows),
        "BothMatch":           total["BothMatch"],
        "CorrectExclusion":    total["CorrectExclusion"],
        "IrrelevantInclusion": total["IrrelevantInclusion"],
        "GeoMiss":             total["GeoMiss"],
        "SystemOnly":          total["SystemOnly"],
    })

    return comparison_rows, summary_rows


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"  -> {path}")


def print_summary(label: str, summary_rows: list[dict]):
    total = [r for r in summary_rows if r["query"] == "TOTAL"][0]
    print(f"\n=== {label} ===")
    for k in ["BothMatch", "CorrectExclusion", "IrrelevantInclusion", "GeoMiss", "SystemOnly"]:
        print(f"  {k:<22}: {total[k]}")


# ── main ──────────────────────────────────────────────────────────────────────

def parse_args():
    args = sys.argv[1:]
    llm_path  = None
    geo_path  = None
    ncbi_path = None
    out_dir   = Path(__file__).parent.parent / "benchmarks-ncbi"

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--llm" and i + 1 < len(args):
            llm_path = Path(args[i + 1]); i += 2
        elif a == "--geo" and i + 1 < len(args):
            geo_path = Path(args[i + 1]); i += 2
        elif a == "--ncbi" and i + 1 < len(args):
            ncbi_path = Path(args[i + 1]); i += 2
        elif a == "--out-dir" and i + 1 < len(args):
            out_dir = Path(args[i + 1]); i += 2
        else:
            i += 1

    # defaults
    root = Path(__file__).parent.parent
    if llm_path  is None: llm_path  = root / "benchmarks-detailed" / "LLM_benchmark_100qs.json"
    if geo_path  is None: geo_path  = root / "benchmarks-detailed" / "run-11-openai" / "batch-output.json"
    if ncbi_path is None: ncbi_path = root / "benchmarks-ncbi" / "batch-output.json"

    for p in (llm_path, geo_path, ncbi_path):
        if not p.exists():
            print(f"ERROR: file not found: {p}", file=sys.stderr)
            sys.exit(1)

    return llm_path, geo_path, ncbi_path, out_dir


def main():
    llm_path, geo_path, ncbi_path, out_dir = parse_args()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"LLM ground truth : {llm_path}")
    print(f"GEOSearch results: {geo_path}")
    print(f"NCBI results     : {ncbi_path}")

    llm_data  = load_llm(llm_path)
    geo_data  = load_system(geo_path)
    ncbi_data = load_system(ncbi_path)

    # ── per-system comparisons ────────────────────────────────────────────────
    comp_fields = ["query","accession","matched","llm_rank","llm_call","llm_confidence","sys_rank","sys_title"]
    summ_fields = ["query","llm_results","sys_results","BothMatch","CorrectExclusion","IrrelevantInclusion","GeoMiss","SystemOnly"]

    geo_comp,  geo_summ  = build_comparison(llm_data, geo_data)
    ncbi_comp, ncbi_summ = build_comparison(llm_data, ncbi_data)

    write_csv(out_dir / "comparison_geo.csv",  comp_fields, geo_comp)
    write_csv(out_dir / "summary_geo.csv",     summ_fields, geo_summ)
    write_csv(out_dir / "comparison_ncbi.csv", comp_fields, ncbi_comp)
    write_csv(out_dir / "summary_ncbi.csv",    summ_fields, ncbi_summ)

    print_summary("GEOSearch", geo_summ)
    print_summary("NCBI Entrez", ncbi_summ)

    # ── head-to-head per query ────────────────────────────────────────────────
    all_queries = sorted(set(llm_data) | set(geo_data) | set(ncbi_data))
    geo_by_q  = {r["query"]: r for r in geo_summ  if r["query"] != "TOTAL"}
    ncbi_by_q = {r["query"]: r for r in ncbi_summ if r["query"] != "TOTAL"}

    h2h_rows = []
    for q in all_queries:
        g = geo_by_q.get(q, {})
        n = ncbi_by_q.get(q, {})
        row = {
            "query":                   q,
            "geo_BothMatch":           g.get("BothMatch", 0),
            "geo_GeoMiss":             g.get("GeoMiss", 0),
            "geo_IrrelevantInclusion": g.get("IrrelevantInclusion", 0),
            "ncbi_BothMatch":          n.get("BothMatch", 0),
            "ncbi_GeoMiss":            n.get("GeoMiss", 0),
            "ncbi_IrrelevantInclusion":n.get("IrrelevantInclusion", 0),
            "geo_advantage":           g.get("BothMatch", 0) - n.get("BothMatch", 0),
        }
        h2h_rows.append(row)

    # sort by geo_advantage descending for paper-friendly display
    h2h_rows.sort(key=lambda r: r["geo_advantage"], reverse=True)

    write_csv(out_dir / "head_to_head.csv", [
        "query",
        "geo_BothMatch","geo_GeoMiss","geo_IrrelevantInclusion",
        "ncbi_BothMatch","ncbi_GeoMiss","ncbi_IrrelevantInclusion",
        "geo_advantage",
    ], h2h_rows)

    print("\n=== Head-to-Head Summary ===")
    geo_total_match  = sum(r["geo_BothMatch"]  for r in h2h_rows)
    ncbi_total_match = sum(r["ncbi_BothMatch"] for r in h2h_rows)
    geo_wins  = sum(1 for r in h2h_rows if r["geo_advantage"] > 0)
    ncbi_wins = sum(1 for r in h2h_rows if r["geo_advantage"] < 0)
    ties      = sum(1 for r in h2h_rows if r["geo_advantage"] == 0)
    print(f"  GEOSearch  total BothMatch : {geo_total_match}")
    print(f"  NCBI       total BothMatch : {ncbi_total_match}")
    print(f"  Queries where GEOSearch wins  : {geo_wins}")
    print(f"  Queries where NCBI wins       : {ncbi_wins}")
    print(f"  Ties                          : {ties}")
    print(f"\nFiles written to {out_dir}/")


if __name__ == "__main__":
    main()
