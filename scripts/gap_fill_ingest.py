#!/usr/bin/env python3
"""
Gap-fill ingestion: fetch all human GSE accessions from NCBI, subtract what's
already in the local DB, and ingest only the missing ones.

Steps:
  1. ESearch NCBI for all human GSEs (pages through using retstart)
  2. Pull existing accessions from local PostgreSQL
  3. Compute the gap (NCBI set - local set)
  4. Feed gap accessions to ingest_by_accessions() in configurable batches

Usage:
    python scripts/gap_fill_ingest.py                   # dry run — print gap count only
    python scripts/gap_fill_ingest.py --run             # actually ingest
    python scripts/gap_fill_ingest.py --run --batch 500 # custom batch size
    python scripts/gap_fill_ingest.py --run --start 10000  # resume from offset N in gap list

Options:
    --run               Execute ingestion (default: dry-run, print gap only)
    --batch INT         Accessions per ingest_by_accessions call (default: 1000)
    --start INT         Skip first N gap accessions (for resuming after failure)
    --query STR         NCBI search query (default: Homo sapiens human GSEs)
    --out FILE          Save gap accession list to file (default: gap_accessions.txt)
"""
import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

DEFAULT_QUERY = "Homo sapiens[orgn] AND gse[Entry Type]"
PAGE_SIZE = 5_000   # IDs per ESearch page
ESUMMARY_BATCH = 200  # ESummary IDs per POST call


def fetch_all_ncbi_ids(client, query: str) -> set[str]:
    """
    Page through NCBI ESearch to collect ALL matching GSE accession strings.
    Returns a set of strings like {'GSE12345', ...}.
    """
    from xml.etree import ElementTree as ET

    # First call: get total count via usehistory
    params = {
        "db": "gds",
        "term": query,
        "retmax": 0,
        "retmode": "json",
        "usehistory": "y",
    }
    resp = client._make_request("esearch.fcgi", {**client._get_common_params(), **params})
    data = resp.json()
    result = data.get("esearchresult", {})
    total = int(result.get("count", 0))
    webenv = result.get("webenv", "")
    query_key = result.get("querykey", "")
    log.info(f"NCBI reports {total:,} total records for query: {query}")

    # Page through using WebEnv + query_key (avoids re-running the search)
    accessions: set[str] = set()
    retstart = 0
    while retstart < total:
        page_params = {
            "db": "gds",
            "WebEnv": webenv,
            "query_key": query_key,
            "retstart": retstart,
            "retmax": PAGE_SIZE,
            "retmode": "json",
        }
        resp = client._make_request("esearch.fcgi", {**client._get_common_params(), **page_params})
        ids = resp.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            break

        # Convert NCBI UIDs to GSE accessions via ESummary using POST
        # to avoid 413/414 URI Too Large errors
        for i in range(0, len(ids), ESUMMARY_BATCH):
            sub_ids = ids[i: i + ESUMMARY_BATCH]
            post_data = {
                **client._get_common_params(),
                "db": "gds",
                "id": ",".join(sub_ids),
                "retmode": "json",
            }
            time.sleep(1.0 / client.rate_limit)
            url = f"{client.base_url}/esummary.fcgi"
            sresp = client.session.post(url, data=post_data, timeout=30)
            sresp.raise_for_status()
            sdata = sresp.json().get("result", {})
            for uid, rec in sdata.items():
                if uid == "uids":
                    continue
                acc = rec.get("accession", "")
                if acc.startswith("GSE"):
                    accessions.add(acc)

        retstart += PAGE_SIZE
        pct = min(100, retstart * 100 // total)
        log.info(f"  Fetched {len(accessions):,} accessions so far ({pct}% of {total:,})")

    log.info(f"NCBI total collected: {len(accessions):,}")
    return accessions


def fetch_local_accessions() -> set[str]:
    """Return the set of GSE accessions already in the local PostgreSQL DB."""
    from db.session import SessionLocal
    from db.models import GSESeries
    from sqlalchemy import func

    db = SessionLocal()
    try:
        rows = db.query(GSESeries.accession).all()
        result = {r[0] for r in rows}
        log.info(f"Local DB has {len(result):,} accessions")
        return result
    finally:
        db.close()


def save_gap(gap: list[str], path: Path) -> None:
    path.write_text("\n".join(gap), encoding="utf-8")
    log.info(f"Gap accession list saved to {path} ({len(gap):,} entries)")


def run_ingestion(gap: list[str], batch_size: int, start_offset: int) -> None:
    from db.session import SessionLocal
    from geo_ingest.ingest_pipeline import IngestionPipeline

    if start_offset:
        log.info(f"Resuming: skipping first {start_offset:,} gap accessions")
        gap = gap[start_offset:]

    total = len(gap)
    log.info(f"Starting ingestion of {total:,} accessions in batches of {batch_size}")

    db = SessionLocal()
    try:
        pipeline = IngestionPipeline(db)
        ingested = 0
        errors = 0

        for batch_start in range(0, total, batch_size):
            batch = gap[batch_start: batch_start + batch_size]
            batch_num = batch_start // batch_size + 1
            total_batches = (total + batch_size - 1) // batch_size
            log.info(f"Batch {batch_num}/{total_batches}: {len(batch)} accessions "
                     f"(overall {batch_start + start_offset}/{total + start_offset})")
            t0 = time.time()
            try:
                result = pipeline.ingest_by_accessions(batch, skip_existing=True)
                ingested += result.get("success", 0)
                errors += result.get("errors", 0)
                elapsed = time.time() - t0
                log.info(f"  Done in {elapsed:.1f}s — success={result.get('success',0)} "
                         f"errors={result.get('errors',0)} skipped={result.get('skipped',0)}")
            except Exception as e:
                log.error(f"  Batch {batch_num} failed: {e}")
                errors += len(batch)

        log.info(f"Ingestion complete. Total ingested={ingested:,} errors={errors:,}")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", action="store_true",
                        help="Execute ingestion (default: dry-run)")
    parser.add_argument("--batch", type=int, default=1000,
                        help="Accessions per ingest batch (default: 1000)")
    parser.add_argument("--start", type=int, default=0,
                        help="Skip first N gap accessions, for resuming (default: 0)")
    parser.add_argument("--query", type=str, default=DEFAULT_QUERY,
                        help=f"NCBI search query (default: '{DEFAULT_QUERY}')")
    parser.add_argument("--out", type=str, default="gap_accessions.txt",
                        help="File to save gap accession list (default: gap_accessions.txt)")
    parser.add_argument("--from-file", type=str, default=None,
                        help="Skip NCBI fetch — load gap list from this file instead")
    args = parser.parse_args()

    if args.from_file:
        # Resume mode: gap list already computed
        gap_path = Path(args.from_file)
        if not gap_path.exists():
            log.error(f"File not found: {gap_path}")
            sys.exit(1)
        gap = [line.strip() for line in gap_path.read_text().splitlines() if line.strip()]
        log.info(f"Loaded {len(gap):,} gap accessions from {gap_path}")
    else:
        from geo_ingest.ncbi_client import NCBIClient
        client = NCBIClient()

        ncbi_set = fetch_all_ncbi_ids(client, args.query)
        local_set = fetch_local_accessions()

        gap_set = ncbi_set - local_set
        gap = sorted(gap_set, key=lambda x: int(x[3:]) if x[3:].isdigit() else 0)

        log.info(f"Gap: {len(gap):,} accessions to ingest "
                 f"(NCBI={len(ncbi_set):,} - local={len(local_set):,})")

        # Always save the gap list so we can resume if needed
        out_path = Path(args.out)
        save_gap(gap, out_path)

    if not args.run:
        log.info("Dry-run mode — pass --run to start ingestion")
        log.info(f"Estimated time: ~{len(gap) / 500 / 3600:.1f} hours at 3 req/s")
        log.info(f"Estimated embedding cost: ~${len(gap) * 300 / 1_000_000 * 0.13:.2f}")
        return

    run_ingestion(gap, args.batch, args.start)


if __name__ == "__main__":
    main()
