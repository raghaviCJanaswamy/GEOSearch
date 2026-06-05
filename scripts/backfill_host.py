"""
Backfill overall_design by scraping the NCBI GEO acc.cgi HTML page.
Uses www.ncbi.nlm.nih.gov — NOT ftp.ncbi.nlm.nih.gov — so unaffected by FTP IP blocks.
Response is ~30-60 KB of HTML per page (much lighter than full family.soft.gz).

Usage:
    cd /Users/ragavahini/Edu-SDSU-Workspace/GEOSearch
    python scripts/backfill_host.py
    python scripts/backfill_host.py --workers 3 --rate-limit 3.0
    python scripts/backfill_host.py --dry-run   # test without writing
"""
import argparse
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import psycopg2
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# ── DB connection ────────────────────────────────────────────────────────────
DB_DSN = os.getenv(
    "POSTGRES_DSN",
    "postgresql://geouser:geopass@localhost:5432/geosearch",
)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "GEOSearch/1.0 backfill"})

# Regex to extract overall design from GEO HTML page table cell
# Matches: <td ...>Overall design</td>\n<td ...>TEXT<br></td>
_OD_RE = re.compile(
    r'Overall design</td>\s*<td[^>]*>(.*?)<br>',
    re.IGNORECASE | re.DOTALL,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def fetch_overall_design(accession: str, retries: int = 3) -> str | None:
    """Fetch overall_design from the NCBI GEO HTML page for a GSE accession."""
    url = f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={accession}"
    for attempt in range(1, retries + 1):
        try:
            resp = SESSION.get(url, timeout=30)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()

            m = _OD_RE.search(resp.text)
            if not m:
                return None

            # Strip any remaining HTML tags and whitespace
            raw = m.group(1)
            text = re.sub(r'<[^>]+>', ' ', raw)
            text = re.sub(r'\s+', ' ', text).strip()
            return text if text else None

        except Exception as e:
            if attempt < retries:
                wait = 2 ** attempt
                logger.warning(f"{accession}: attempt {attempt} failed ({e}), retrying in {wait}s")
                time.sleep(wait)
            else:
                logger.error(f"{accession}: failed after {retries} attempts: {e}")
                return None
    return None


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--rate-limit", type=float, default=3.0,
                    help="Requests per second (default 3.0)")
    ap.add_argument("--batch-size", type=int, default=500)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    delay = 1.0 / args.rate_limit

    # Get pending accessions
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    cur.execute("""
        SELECT accession FROM gse_series
        WHERE overall_design IS NULL OR overall_design = ''
        ORDER BY accession
    """)
    accessions = [row[0] for row in cur.fetchall()]
    conn.close()

    total = len(accessions)
    logger.info(f"Records to backfill: {total}")

    if total == 0:
        logger.info("Nothing to do.")
        return

    success = errors = not_found = 0
    done = 0

    def process(acc):
        time.sleep(delay)
        return acc, fetch_overall_design(acc)

    # Process in batches
    for batch_start in range(0, total, args.batch_size):
        batch = accessions[batch_start: batch_start + args.batch_size]
        logger.info(f"--- Batch starting at {batch_start+1} of {total} ---")

        conn = psycopg2.connect(DB_DSN)
        cur = conn.cursor()

        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(process, acc): acc for acc in batch}
            for future in as_completed(futures):
                done += 1
                accession, overall_design = future.result()

                if overall_design is None:
                    not_found += 1
                    if done % 50 == 0:
                        logger.info(f"[{done}/{total}] {accession}: not found")
                    continue

                if args.dry_run:
                    logger.info(f"[{done}/{total}] {accession} (dry-run): {overall_design[:80]}")
                    success += 1
                    continue

                try:
                    cur.execute(
                        "UPDATE gse_series SET overall_design = %s WHERE accession = %s",
                        (overall_design, accession),
                    )
                    conn.commit()
                    success += 1
                    if done % 100 == 0 or done <= 10:
                        logger.info(f"[{done}/{total}] updated {accession} ({len(overall_design)} chars)")
                except Exception as e:
                    conn.rollback()
                    errors += 1
                    logger.error(f"[{done}/{total}] DB error {accession}: {e}")

        cur.close()
        conn.close()
        logger.info(
            f"Batch done — total so far: {success} updated, "
            f"{not_found} not found, {errors} errors"
        )

    print(f"\n{'='*55}")
    print(f"Backfill complete")
    print(f"  Updated:   {success}")
    print(f"  Not found: {not_found}")
    print(f"  Errors:    {errors}")
    print(f"{'='*55}")


if __name__ == "__main__":
    main()
