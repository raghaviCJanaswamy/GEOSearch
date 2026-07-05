"""
Fast backfill of overall_design using NCBI GEO text view API.
Fetches overall_design from acc.cgi?form=text — lightweight text endpoint.

Speed: ~5-10 records/sec with 8 workers = ~4-6 hours for 128K records.

Usage:
    # Dry run (test first)
    POSTGRES_HOST=localhost python scripts/backfill_fast.py --dry-run --limit 20

    # Full backfill (run from Mac, not inside Docker)
    POSTGRES_HOST=localhost python scripts/backfill_fast.py --workers 8 --batch-size 200

    # Only missing records from a file
    POSTGRES_HOST=localhost python scripts/backfill_fast.py --from-file compare_GSE_NCBI/still_missing_from_geosearch.txt

    # Resume after interruption (skips records that already have overall_design)
    POSTGRES_HOST=localhost python scripts/backfill_fast.py --workers 8
"""
import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import settings
from db import init_db, get_db
from db.models import GSESeries

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

GEO_TEXT_URL = "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "GEOSearch/2.0 backfill_fast (ragavahini@sdsu.edu)"})


def fetch_overall_design(accession: str, retries: int = 3) -> str | None:
    """
    Fetch overall_design for one GSE using GEO text view.
    Returns the overall_design string or None if not found.
    """
    params = {
        "acc": accession,
        "targ": "self",
        "form": "text",
        "view": "brief",
    }
    for attempt in range(1, retries + 1):
        try:
            resp = SESSION.get(GEO_TEXT_URL, params=params, timeout=20)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()

            lines = []
            collecting = False
            for line in resp.text.splitlines():
                if line.startswith("!Series_overall_design"):
                    value = line.split("=", 1)[1].strip() if "=" in line else ""
                    if value:
                        lines.append(value)
                    collecting = True
                elif collecting:
                    if line.startswith("!"):
                        break  # next field started
                    stripped = line.strip()
                    if stripped:
                        lines.append(stripped)

            return " ".join(lines).strip() if lines else None

        except Exception as e:
            if attempt < retries:
                wait = 2 ** attempt
                logger.debug(f"{accession}: attempt {attempt} failed ({e}), retrying in {wait}s")
                time.sleep(wait)
            else:
                logger.warning(f"{accession}: all {retries} attempts failed: {e}")
                return None
    return None


def get_accessions(db, limit: int | None, from_file: str | None, refill_all: bool) -> list[str]:
    """Get accessions to backfill from DB or file."""
    if from_file:
        path = Path(from_file)
        if not path.exists():
            logger.error(f"File not found: {from_file}")
            sys.exit(1)
        accessions = [
            line.strip() for line in path.read_text().splitlines()
            if line.strip().startswith("GSE")
        ]
        logger.info(f"Loaded {len(accessions)} accessions from {from_file}")
        return accessions[:limit] if limit else accessions

    q = db.query(GSESeries.accession)
    if not refill_all:
        q = q.filter(
            (GSESeries.overall_design == None) |  # noqa: E711
            (GSESeries.overall_design == "")
        )
    q = q.order_by(GSESeries.accession)
    if limit:
        q = q.limit(limit)

    accessions = [row[0] for row in q.all()]
    logger.info(f"Found {len(accessions)} accessions to backfill")
    return accessions


def run_backfill(accessions, workers, rate_limit, dry_run, db) -> dict:
    """Concurrent backfill with rate limiting."""
    stats = {"updated": 0, "no_design": 0, "errors": 0, "total": len(accessions)}
    delay = 1.0 / rate_limit
    total = len(accessions)
    done = 0
    pending_updates = {}

    def fetch_one(accession):
        time.sleep(delay)
        return accession, fetch_overall_design(accession)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch_one, acc): acc for acc in accessions}

        for future in as_completed(futures):
            done += 1
            try:
                accession, overall_design = future.result()
            except Exception as e:
                accession = futures[future]
                logger.error(f"[{done}/{total}] {accession}: unexpected error: {e}")
                stats["errors"] += 1
                continue

            if not overall_design:
                stats["no_design"] += 1
                continue

            if dry_run:
                logger.info(f"[{done}/{total}] {accession} (dry-run): {overall_design[:120]}")
                stats["updated"] += 1
                continue

            pending_updates[accession] = overall_design

            # Commit in chunks of 100 to balance memory vs DB round-trips
            if len(pending_updates) >= 100:
                _flush(pending_updates, db, stats, done, total)
                pending_updates.clear()

            if done % 500 == 0 or done == total:
                pct = done / total * 100
                logger.info(
                    f"Progress: {done}/{total} ({pct:.1f}%) | "
                    f"updated={stats['updated']} no_design={stats['no_design']} errors={stats['errors']}"
                )

    # Flush remaining
    if pending_updates and not dry_run:
        _flush(pending_updates, db, stats, done, total)

    return stats


def _flush(updates: dict, db, stats: dict, done: int, total: int):
    """Bulk update a batch of accessions in one DB transaction."""
    try:
        for accession, overall_design in updates.items():
            db.query(GSESeries).filter(
                GSESeries.accession == accession
            ).update({"overall_design": overall_design})
        db.commit()
        stats["updated"] += len(updates)
        logger.info(f"  Committed {len(updates)} updates to DB [{done}/{total}]")
    except Exception as e:
        db.rollback()
        stats["errors"] += len(updates)
        logger.error(f"  Batch commit failed: {e}")


def main():
    ap = argparse.ArgumentParser(description="Fast overall_design backfill via GEO text view")
    ap.add_argument("--workers", type=int, default=6,
                    help="Parallel fetch threads (default: 6)")
    ap.add_argument("--rate-limit", type=float, default=5.0,
                    help="Requests per second total (default: 5.0)")
    ap.add_argument("--batch-size", type=int, default=200,
                    help="DB commit batch size (default: 200)")
    ap.add_argument("--limit", type=int, default=None,
                    help="Only process this many records (for testing)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Fetch but do not write to DB")
    ap.add_argument("--all", action="store_true",
                    help="Re-process all records including those with existing overall_design")
    ap.add_argument("--from-file", type=str, default=None,
                    help="Path to file with one GSE accession per line")
    args = ap.parse_args()

    init_db()
    db = next(get_db())

    accessions = get_accessions(db, args.limit, args.from_file, args.all)

    if not accessions:
        logger.info("Nothing to backfill.")
        db.close()
        return

    total = len(accessions)
    eta_sec = total / args.rate_limit
    logger.info(
        f"Starting backfill: {total} records | "
        f"workers={args.workers} | rate={args.rate_limit} req/s | "
        f"ETA ~{eta_sec/3600:.1f} hours"
    )

    stats = run_backfill(
        accessions=accessions,
        workers=args.workers,
        rate_limit=args.rate_limit,
        dry_run=args.dry_run,
        db=db,
    )

    db.close()

    print(f"\n{'='*55}")
    print(f"Backfill {'(DRY RUN) ' if args.dry_run else ''}complete")
    print(f"  Total processed: {stats['total']}")
    print(f"  Updated:         {stats['updated']}")
    print(f"  No design found: {stats['no_design']}")
    print(f"  Errors:          {stats['errors']}")
    print(f"{'='*55}")


if __name__ == "__main__":
    main()
