#!/usr/bin/env python3
"""
Fetch top-N GEO datasets from NCBI Entrez for a set of queries.

Uses NCBI eutils (esearch + esummary) — no API key required, but rate-limited
to 3 req/s without one. Set NCBI_API_KEY in .env for 10 req/s.

Input:  benchmarks-detailed/LLM_benchmark_100qs.json  (same format as GEOSearch benchmark)
Output: benchmarks-ncbi/batch-output.json             (same schema as GEOSearch batch-output.json)

Usage:
    python scripts/ncbi_search.py
    python scripts/ncbi_search.py --top 100
    python scripts/ncbi_search.py --json benchmarks-detailed/LLM_benchmark_100qs.json --top 100
    python scripts/ncbi_search.py --out-dir benchmarks-ncbi
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

# ── config ────────────────────────────────────────────────────────────────────
NCBI_BASE   = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
NCBI_DB     = "gds"           # GEO DataSets
DEFAULT_TOP = 100             # results per query
DELAY_NO_KEY   = 0.34         # ~3 req/s without API key
DELAY_WITH_KEY = 0.11         # ~10 req/s with API key


def _get(url: str, retries: int = 3) -> bytes:
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return r.read()
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)


def ncbi_search(query: str, top: int, api_key: str | None) -> list[str]:
    """Return list of GEO UIDs (up to `top`) for the query."""
    params = {
        "db":       NCBI_DB,
        "term":     query,
        "retmax":   top,
        "retmode":  "json",
        "usehistory": "n",
    }
    if api_key:
        params["api_key"] = api_key
    url = f"{NCBI_BASE}/esearch.fcgi?{urllib.parse.urlencode(params)}"
    data = json.loads(_get(url))
    return data.get("esearchresult", {}).get("idlist", [])


def ncbi_summary(uids: list[str], api_key: str | None) -> list[dict]:
    """Fetch eSummary records for a list of GEO UIDs."""
    if not uids:
        return []
    params = {
        "db":      NCBI_DB,
        "id":      ",".join(uids),
        "retmode": "json",
    }
    if api_key:
        params["api_key"] = api_key
    url = f"{NCBI_BASE}/esummary.fcgi?{urllib.parse.urlencode(params)}"
    data = json.loads(_get(url))
    result_map = data.get("result", {})
    records = []
    for uid in uids:
        rec = result_map.get(uid)
        if rec and isinstance(rec, dict):
            records.append(rec)
    return records


def record_to_result(rank: int, rec: dict) -> dict:
    """Normalise an eSummary record to the same schema as GEOSearch results."""
    acc = rec.get("accession", "")
    # eSummary uses 'gse' accession for series; keep only GSE* records
    entry_type = rec.get("entrytype", "")
    return {
        "rank":            rank,
        "accession":       acc,
        "title":           rec.get("title", ""),
        "summary":         rec.get("summary", ""),
        "organisms":       [rec.get("taxon", "")],
        "tech_type":       rec.get("gdstype", ""),
        "sample_count":    rec.get("n_samples"),
        "submission_date": (rec.get("pdat") or "")[:10],
        "geo_url":         f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={acc}",
        "entry_type":      entry_type,
        "tier":            "unjudged",
        "reason":          "",
    }


def run_query(query: str, top: int, api_key: str | None, delay: float) -> dict:
    t0 = time.time()
    uids = ncbi_search(query, top, api_key)
    time.sleep(delay)
    records = ncbi_summary(uids, api_key)
    time.sleep(delay)
    elapsed = time.time() - t0

    results = [record_to_result(i + 1, r) for i, r in enumerate(records)]
    return {
        "query":         query,
        "total_results": len(results),
        "source":        "ncbi_entrez",
        "elapsed":       round(elapsed, 3),
        "results":       results,
    }


def parse_args():
    args = sys.argv[1:]
    json_path = Path(__file__).parent.parent / "benchmarks-detailed" / "LLM_benchmark_100qs.json"
    out_dir   = Path(__file__).parent.parent / "benchmarks-ncbi"
    top       = DEFAULT_TOP

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--json" and i + 1 < len(args):
            json_path = Path(args[i + 1]); i += 2
        elif a == "--top" and i + 1 < len(args):
            top = int(args[i + 1]); i += 2
        elif a == "--out-dir" and i + 1 < len(args):
            out_dir = Path(args[i + 1]); i += 2
        else:
            i += 1
    return json_path, out_dir, top


def main():
    json_path, out_dir, top = parse_args()
    out_dir.mkdir(parents=True, exist_ok=True)

    api_key = os.environ.get("NCBI_API_KEY") or None
    delay   = DELAY_WITH_KEY if api_key else DELAY_NO_KEY

    data    = json.loads(json_path.read_text(encoding="utf-8"))
    queries = [obj["query"].strip() for obj in data if obj.get("query", "").strip()]
    # deduplicate preserving order
    seen: set[str] = set()
    unique_queries = []
    for q in queries:
        if q not in seen:
            unique_queries.append(q)
            seen.add(q)

    print(f"Queries     : {len(unique_queries)}")
    print(f"Top K       : {top}")
    print(f"API key     : {'yes' if api_key else 'no (3 req/s limit)'}")
    print(f"Output dir  : {out_dir}")
    print()

    batch: list[dict] = []
    for idx, query in enumerate(unique_queries, 1):
        print(f"[{idx}/{len(unique_queries)}] {query!r} ... ", end="", flush=True)
        try:
            result = run_query(query, top, api_key, delay)
            batch.append(result)
            print(f"{result['total_results']} results in {result['elapsed']:.2f}s")
        except Exception as e:
            print(f"FAILED: {e}", file=sys.stderr)

    batch_path = out_dir / "batch-output.json"
    batch_path.write_text(json.dumps(batch, indent=2), encoding="utf-8")
    print(f"\nBatch file  : {batch_path}  ({len(batch)} queries)")
    print("Done.")


if __name__ == "__main__":
    main()
