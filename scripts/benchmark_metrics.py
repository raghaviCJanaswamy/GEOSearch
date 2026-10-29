#!/usr/bin/env python3
"""
Compute IR metrics for GEOSearch vs NCBI Entrez using LLM ground truth.

Metrics (per query, then macro-averaged):
    Precision@K    — fraction of top-K results that are relevant
    Recall@K       — fraction of relevant datasets retrieved in top-K
    NDCG@K         — normalised discounted cumulative gain (graded: relevant=1, borderline=0.5)
    MRR            — mean reciprocal rank of first relevant result

K values: 10, 20, 50  (configurable with --k)

LLM relevance mapping:
    relevant   -> gain = 1.0
    borderline -> gain = 0.5
    not_relevant -> gain = 0.0
    (absent from LLM pool) -> gain = 0.0

Usage:
    python scripts/benchmark_metrics.py
    python scripts/benchmark_metrics.py --k 10 20 50 100
    python scripts/benchmark_metrics.py \\
        --llm  benchmarks-detailed/LLM_benchmark_100qs.json \\
        --geo  benchmarks-detailed/run-11-openai/batch-output.json \\
        --ncbi benchmarks-ncbi/batch-output.json \\
        --out-dir benchmarks-ncbi
"""
import csv
import json
import math
import sys
from pathlib import Path


# ── data loading ──────────────────────────────────────────────────────────────

def load_llm(path: Path) -> dict[str, dict[str, float]]:
    """{ query -> { accession -> gain } }"""
    GAIN = {"relevant": 1.0, "borderline": 0.5, "not_relevant": 0.0}
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, dict[str, float]] = {}
    for obj in data:
        q = obj.get("query", "").strip()
        if not q:
            continue
        gains = {}
        for r in obj.get("results", []):
            acc  = r.get("accession", "")
            call = r.get("call", "not_relevant").strip()
            gains[acc] = GAIN.get(call, 0.0)
        out[q] = gains
    return out


def load_system(path: Path) -> dict[str, list[str]]:
    """{ query -> [accession_rank1, accession_rank2, ...] }  ordered by rank"""
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, list[str]] = {}
    for obj in data:
        q = obj.get("query", "").strip()
        if not q:
            continue
        results = obj.get("results")
        if results is None and "output" in obj:
            results = obj["output"][0].get("results", [])
        ranked = sorted(results or [], key=lambda r: r.get("rank", 9999))
        out[q] = [r["accession"] for r in ranked]
    return out


# ── metric functions ──────────────────────────────────────────────────────────

def precision_at_k(ranked: list[str], gains: dict[str, float], k: int) -> float:
    top = ranked[:k]
    if not top:
        return 0.0
    relevant = sum(1 for acc in top if gains.get(acc, 0.0) > 0)
    return relevant / len(top)


def recall_at_k(ranked: list[str], gains: dict[str, float], k: int) -> float:
    total_relevant = sum(1 for g in gains.values() if g > 0)
    if total_relevant == 0:
        return 0.0
    retrieved = sum(1 for acc in ranked[:k] if gains.get(acc, 0.0) > 0)
    return retrieved / total_relevant


def dcg_at_k(ranked: list[str], gains: dict[str, float], k: int) -> float:
    score = 0.0
    for i, acc in enumerate(ranked[:k], start=1):
        g = gains.get(acc, 0.0)
        score += g / math.log2(i + 1)
    return score


def ndcg_at_k(ranked: list[str], gains: dict[str, float], k: int) -> float:
    dcg = dcg_at_k(ranked, gains, k)
    # ideal: sort all known gains descending
    ideal_ranked = sorted(gains.values(), reverse=True)
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal_ranked[:k]) if g > 0)
    if idcg == 0:
        return 0.0
    return dcg / idcg


def reciprocal_rank(ranked: list[str], gains: dict[str, float]) -> float:
    for i, acc in enumerate(ranked, start=1):
        if gains.get(acc, 0.0) > 0:
            return 1.0 / i
    return 0.0


# ── main ──────────────────────────────────────────────────────────────────────

def compute_metrics(sys_data: dict[str, list[str]], llm_data: dict[str, dict[str, float]],
                    ks: list[int]) -> tuple[list[dict], dict]:
    """Returns (per_query_rows, macro_averages)."""
    rows: list[dict] = []
    all_queries = sorted(set(llm_data) & set(sys_data))   # intersection only

    for q in all_queries:
        ranked = sys_data[q]
        gains  = llm_data[q]
        row: dict = {"query": q}
        for k in ks:
            row[f"P@{k}"]    = round(precision_at_k(ranked, gains, k), 4)
            row[f"R@{k}"]    = round(recall_at_k(ranked, gains, k), 4)
            row[f"NDCG@{k}"] = round(ndcg_at_k(ranked, gains, k), 4)
        row["MRR"] = round(reciprocal_rank(ranked, gains), 4)
        rows.append(row)

    # macro averages
    macro: dict = {"query": "MACRO_AVG"}
    for k in ks:
        macro[f"P@{k}"]    = round(sum(r[f"P@{k}"]    for r in rows) / len(rows), 4) if rows else 0
        macro[f"R@{k}"]    = round(sum(r[f"R@{k}"]    for r in rows) / len(rows), 4) if rows else 0
        macro[f"NDCG@{k}"] = round(sum(r[f"NDCG@{k}"] for r in rows) / len(rows), 4) if rows else 0
    macro["MRR"] = round(sum(r["MRR"] for r in rows) / len(rows), 4) if rows else 0

    return rows, macro


def parse_args():
    args = sys.argv[1:]
    root      = Path(__file__).parent.parent
    llm_path  = root / "benchmarks-detailed" / "LLM_benchmark_100qs.json"
    geo_path  = root / "benchmarks-detailed" / "run-11-openai" / "batch-output.json"
    ncbi_path = root / "benchmarks-ncbi" / "batch-output.json"
    out_dir   = root / "benchmarks-ncbi"
    ks        = [10, 20, 50]

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--llm"  and i + 1 < len(args): llm_path  = Path(args[i+1]); i += 2
        elif a == "--geo"  and i + 1 < len(args): geo_path  = Path(args[i+1]); i += 2
        elif a == "--ncbi" and i + 1 < len(args): ncbi_path = Path(args[i+1]); i += 2
        elif a == "--out-dir" and i + 1 < len(args): out_dir = Path(args[i+1]); i += 2
        elif a == "--k":
            ks = []
            i += 1
            while i < len(args) and args[i].isdigit():
                ks.append(int(args[i])); i += 1
        else:
            i += 1

    for p in (llm_path, geo_path, ncbi_path):
        if not p.exists():
            print(f"ERROR: file not found: {p}", file=sys.stderr)
            sys.exit(1)

    return llm_path, geo_path, ncbi_path, out_dir, ks


def main():
    llm_path, geo_path, ncbi_path, out_dir, ks = parse_args()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"LLM ground truth : {llm_path}")
    print(f"GEOSearch        : {geo_path}")
    print(f"NCBI             : {ncbi_path}")
    print(f"K values         : {ks}")
    print()

    llm_data  = load_llm(llm_path)
    geo_data  = load_system(geo_path)
    ncbi_data = load_system(ncbi_path)

    geo_rows,  geo_macro  = compute_metrics(geo_data,  llm_data, ks)
    ncbi_rows, ncbi_macro = compute_metrics(ncbi_data, llm_data, ks)

    fields = ["query"] + [f"{m}@{k}" for k in ks for m in ("P","R","NDCG")] + ["MRR"]
    # fix field order to P@k R@k NDCG@k per k
    fields = ["query"]
    for k in ks:
        fields += [f"P@{k}", f"R@{k}", f"NDCG@{k}"]
    fields += ["MRR"]

    def write(path, rows, macro):
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
            w.writerow(macro)
        print(f"  -> {path}")

    write(out_dir / "metrics_geo.csv",  geo_rows,  geo_macro)
    write(out_dir / "metrics_ncbi.csv", ncbi_rows, ncbi_macro)

    # ── combined summary table (paper-ready) ──────────────────────────────────
    print("\n=== Macro-Average Metrics ===")
    print(f"{'Metric':<12}  {'GEOSearch':>12}  {'NCBI Entrez':>12}  {'Delta':>10}")
    print("-" * 52)
    for k in ks:
        for m in ("P", "R", "NDCG"):
            key = f"{m}@{k}"
            g = geo_macro[key]
            n = ncbi_macro[key]
            d = round(g - n, 4)
            sign = "+" if d >= 0 else ""
            print(f"{key:<12}  {g:>12.4f}  {n:>12.4f}  {sign}{d:>9.4f}")
    g = geo_macro["MRR"]; n = ncbi_macro["MRR"]; d = round(g-n,4)
    sign = "+" if d >= 0 else ""
    print(f"{'MRR':<12}  {g:>12.4f}  {n:>12.4f}  {sign}{d:>9.4f}")

    # combined CSV for easy import into paper tables
    combined_path = out_dir / "metrics_combined.csv"
    with open(combined_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Metric", "GEOSearch", "NCBI_Entrez", "Delta"])
        for k in ks:
            for m in ("P", "R", "NDCG"):
                key = f"{m}@{k}"
                g = geo_macro[key]; n = ncbi_macro[key]
                w.writerow([key, g, n, round(g-n, 4)])
        g = geo_macro["MRR"]; n = ncbi_macro["MRR"]
        w.writerow(["MRR", g, n, round(g-n, 4)])
    print(f"\n  -> {combined_path}")
    print(f"\nFiles written to {out_dir}/")


if __name__ == "__main__":
    main()
