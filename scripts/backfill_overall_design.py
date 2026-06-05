"""
Backfill overall_design for all GSE records that have it empty.
Fetches from NCBI GEO SOFT files via HTTPS.

Usage:
    # Inside Docker (recommended):
    docker exec geosearch-app python scripts/backfill_overall_design.py

    # With options:
    docker exec geosearch-app python scripts/backfill_overall_design.py --batch-size 50 --workers 4
    docker exec geosearch-app python scripts/backfill_overall_design.py --accession GSE324934  # single record
"""
import argparse
import gzip
import io
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import get_db, init_db
from db.models import GSESeries

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "GEOSearch/1.0 (backfill_overall_design)"})


# ---------------------------------------------------------------------------
# FTP path derivation
# ---------------------------------------------------------------------------

def _ftp_path(accession: str) -> str:
    """
    Derive the NCBI FTP HTTPS URL for a GSE SOFT file.
    e.g. GSE324934 -> .../GSE324nnn/GSE324934/soft/GSE324934_family.soft.gz
    """
    # Replace last 3 digits with 'nnn'
    stub = accession[:-3] + "nnn"
    return (
        f"https://ftp.ncbi.nlm.nih.gov/geo/series/{stub}/{accession}"
        f"/soft/{accession}_family.soft.gz"
    )


# ---------------------------------------------------------------------------
# Fetch overall_design from SOFT file
# ---------------------------------------------------------------------------

def fetch_overall_design(accession: str, retries: int = 3) -> str | None:
    """
    Download the SOFT file for a GSE accession and extract overall_design.
    Returns None if not found or on error.
    """
    url = _ftp_path(accession)

    for attempt in range(1, retries + 1):
        try:
            resp = SESSION.get(url, timeout=30, stream=True)
            if resp.status_code == 404:
                logger.warning(f"{accession}: SOFT file not found (404)")
                return None
            resp.raise_for_status()

            # Decompress and scan for overall_design — stop early once found
            buf = io.BytesIO(resp.content)
            with gzip.open(buf, "rt", encoding="utf-8", errors="replace") as f:
                lines = []
                for line in f:
                    if line.startswith("!Series_overall_design"):
                        # May span multiple lines with continuation
                        value = line.split("=", 1)[1].strip()
                        lines.append(value)
                    elif lines and line.startswith("!"):
                        # Next field started — stop collecting
                        break
                    elif lines:
                        lines.append(line.strip())

            if lines:
                return " ".join(lines).strip()

            logger.debug(f"{accession}: overall_design not found in SOFT file")
            return None

        except Exception as e:
            if attempt < retries:
                wait = 2 ** attempt
                logger.warning(f"{accession}: attempt {attempt} failed ({e}), retrying in {wait}s")
                time.sleep(wait)
            else:
                logger.error(f"{accession}: all {retries} attempts failed: {e}")
                return None

    return None


# ---------------------------------------------------------------------------
# Backfill logic
# ---------------------------------------------------------------------------

def backfill(
    accessions: list[str],
    workers: int = 4,
    rate_limit_qps: float = 3.0,
    dry_run: bool = False,
) -> dict:
    """Fetch and store overall_design for a list of accessions."""
    stats = {"success": 0, "not_found": 0, "errors": 0, "skipped": 0}
    total = len(accessions)
    delay = 1.0 / rate_limit_qps

    db = next(get_db())

    def process(accession: str) -> tuple[str, str | None]:
        time.sleep(delay)
        return accession, fetch_overall_design(accession)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(process, acc): acc for acc in accessions}
        done = 0
        for future in as_completed(futures):
            done += 1
            accession, overall_design = future.result()

            if overall_design is None:
                stats["not_found"] += 1
                logger.info(f"[{done}/{total}] {accession}: not found")
                continue

            if dry_run:
                logger.info(f"[{done}/{total}] {accession} (dry-run): {overall_design[:80]}")
                stats["success"] += 1
                continue

            try:
                db.query(GSESeries).filter(
                    GSESeries.accession == accession
                ).update({"overall_design": overall_design})
                db.commit()
                stats["success"] += 1
                logger.info(f"[{done}/{total}] {accession}: updated ({len(overall_design)} chars)")
            except Exception as e:
                db.rollback()
                stats["errors"] += 1
                logger.error(f"[{done}/{total}] {accession}: DB update failed: {e}")

    db.close()
    return stats


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Backfill overall_design from NCBI SOFT files")
    ap.add_argument("--batch-size", type=int, default=500,
                    help="Records per batch (default: 500)")
    ap.add_argument("--workers", type=int, default=4,
                    help="Parallel download threads (default: 4)")
    ap.add_argument("--rate-limit", type=float, default=3.0,
                    help="Requests per second per worker (default: 3.0)")
    ap.add_argument("--accession", type=str, default=None,
                    help="Backfill a single accession only")
    ap.add_argument("--dry-run", action="store_true",
                    help="Fetch but do not write to DB")
    ap.add_argument("--all", action="store_true",
                    help="Re-process all records, even those with existing overall_design")
    args = ap.parse_args()

    init_db()
    db = next(get_db())

    if args.accession:
        accessions = [args.accession]
    else:
        q = db.query(GSESeries.accession)
        if not args.all:
            q = q.filter(
                (GSESeries.overall_design == None) |
                (GSESeries.overall_design == "")
            )
        accessions = [row[0] for row in q.order_by(GSESeries.accession).all()]

    db.close()

    total = len(accessions)
    logger.info(f"Records to backfill: {total}")

    if total == 0:
        logger.info("Nothing to do.")
        return

    overall_stats = {"success": 0, "not_found": 0, "errors": 0, "skipped": 0}

    # Process in batches to log progress
    batch_size = args.batch_size
    for i in range(0, total, batch_size):
        batch = accessions[i:i + batch_size]
        logger.info(f"--- Batch {i // batch_size + 1}: records {i+1}–{i+len(batch)} of {total} ---")
        stats = backfill(
            accessions=batch,
            workers=args.workers,
            rate_limit_qps=args.rate_limit,
            dry_run=args.dry_run,
        )
        for k in overall_stats:
            overall_stats[k] += stats.get(k, 0)

        logger.info(
            f"Batch done: success={stats['success']} "
            f"not_found={stats['not_found']} errors={stats['errors']}"
        )

    print(f"\n{'='*55}")
    print(f"Backfill complete")
    print(f"  Updated:   {overall_stats['success']}")
    print(f"  Not found: {overall_stats['not_found']}")
    print(f"  Errors:    {overall_stats['errors']}")
    print(f"{'='*55}")


if __name__ == "__main__":
    main()
