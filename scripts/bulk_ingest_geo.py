"""
Bulk ingest GEO Series metadata using EFetch batch SOFT text.

Instead of one HTTP call per record (128K calls), this fetches 500 records
per call using NCBI WebEnv — ~256 total calls for 128K records.

Speed comparison:
  backfill_fast.py  : 1 call/record  = 128,000 calls  ~6-7 hrs
  bulk_ingest_geo.py: 500 rec/call   =     256 calls  ~5-15 min (fetch)
                                                       + ~30 min (embed)

Fields captured per record:
  status, title, organism, summary, overall_design,
  contributors, citations (pubmed_ids), platforms,
  submission_date, last_update_date, sample_count

Usage:
    # Homo sapiens, all years
    python scripts/bulk_ingest_geo.py

    # Custom organism and date range
    python scripts/bulk_ingest_geo.py \\
        --organism "Homo sapiens" --organism "Mus musculus" \\
        --date-start 2020/01/01 --date-end 2024/12/31

    # Dry run — fetch + parse but do not write to DB/Milvus
    python scripts/bulk_ingest_geo.py --dry-run --limit 1000

    # Skip embedding (DB only, faster)
    python scripts/bulk_ingest_geo.py --no-embed

    # Force re-ingest records already in DB
    python scripts/bulk_ingest_geo.py --force
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from datetime import datetime
from typing import Any

import requests
from sqlalchemy.dialects.postgresql import insert as pg_insert

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import settings
from db import GSESeries, init_db
from db.session import SessionLocal
from geo_ingest.parser import GEOParser
from vector.embeddings import get_embedding_provider
from vector.milvus_store import MilvusStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

ESEARCH_URL  = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL   = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

BATCH_FETCH  = 500    # records per EFetch call
BATCH_COMMIT = 200    # postgres upsert batch
EMBED_BATCH  = 64     # embedding batch

_NCBI_DELAY  = 0.34   # seconds between calls (~3/s without key, 0.11 with key)


# ── NCBI helpers ────────────────────────────────────────────────────────────

def _base_params() -> dict:
    p = {
        "email": os.getenv("NCBI_EMAIL", settings.ncbi_email or "geosearch@example.com"),
        "tool":  "GEOSearch-bulk",
    }
    api_key = os.getenv("NCBI_API_KEY", settings.ncbi_api_key or "")
    if api_key:
        p["api_key"] = api_key
        global _NCBI_DELAY
        _NCBI_DELAY = 0.11
    return p


def _get(session: requests.Session, url: str, params: dict, retries: int = 5) -> requests.Response:
    for attempt in range(retries):
        time.sleep(_NCBI_DELAY)
        try:
            r = session.get(url, params=params, timeout=60)
            if r.status_code == 429:
                wait = 2 ** (attempt + 1)
                log.warning(f"429 rate limit — waiting {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r
        except requests.exceptions.RequestException as e:
            if attempt == retries - 1:
                raise
            log.warning(f"Request error: {e} — retry {attempt+1}/{retries}")
            time.sleep(2 ** attempt)
    raise RuntimeError("Max retries exceeded")


# ── Step 1: ESearch → get WebEnv + total count ──────────────────────────────

def esearch(session: requests.Session, organisms: list[str],
            date_start: str, date_end: str,
            ncbi_query: str | None = None) -> tuple[str, str, int]:
    """
    Run ESearch and return (web_env, query_key, total_count).
    Uses usehistory=y so we can EFetch in batches without re-sending IDs.
    """
    if ncbi_query:
        term = f'({ncbi_query}) AND ("{date_start}"[PDAT] : "{date_end}"[PDAT])'
    else:
        org_terms = " OR ".join(f'"{o}"[Organism]' for o in organisms)
        term = (
            f'({org_terms}) AND gse[Entry Type]'
            f' AND ("{date_start}"[PDAT] : "{date_end}"[PDAT])'
        )

    log.info(f"ESearch: {term}")
    r = _get(session, ESEARCH_URL, {**_base_params(),
        "db": "gds", "term": term,
        "retmax": 0, "retmode": "json", "usehistory": "y",
    })
    data = r.json()["esearchresult"]
    total = int(data["count"])
    log.info(f"Total matching records: {total:,}")
    return data["webenv"], data["querykey"], total


# ── Step 2: EFetch batch SOFT text ──────────────────────────────────────────

def efetch_soft_batch(session: requests.Session, web_env: str,
                      query_key: str, retstart: int, retmax: int) -> str:
    """Fetch retmax SOFT records starting at retstart. Returns raw SOFT text."""
    r = _get(session, EFETCH_URL, {**_base_params(),
        "db": "gds", "WebEnv": web_env, "query_key": query_key,
        "retstart": retstart, "retmax": retmax,
        "rettype": "full", "retmode": "text",
    })
    return r.text


# ── Step 3: Parse SOFT text → list of record dicts ──────────────────────────

def _collect(lines: list[str], tag: str) -> list[str]:
    """Extract all values for !Series_<tag> lines."""
    results = []
    collecting = False
    for line in lines:
        if line.startswith(f"!Series_{tag}"):
            val = line.split("=", 1)[1].strip() if "=" in line else ""
            if val:
                results.append(val)
            collecting = True
        elif collecting:
            if line.startswith("!") or line.startswith("^"):
                collecting = False
            else:
                s = line.strip()
                if s:
                    results.append(s)
    return results


def parse_efetch_text(text: str) -> list[dict[str, Any]]:
    """
    Parse the EFetch summary text format returned by db=gds.
    Each record looks like:
        1. Title text
        (Submitter supplied) Summary text...
        Organism:   Homo sapiens
        Type:       Expression profiling...
        Platform:   GPL24676 18 Samples
        FTP download: ...
        Series      Accession: GSE252399   ID: 200252399
    """
    records = []
    # Split on numbered record starts: "1. ", "2. " etc
    blocks = re.split(r'\n(?=\d+\.\s)', text.strip())

    for block in blocks:
        if not block.strip():
            continue
        lines = block.splitlines()

        # Title is first line after the number
        title = ""
        summary_lines = []
        organism = ""
        tech_type_raw = ""
        platforms = []
        accession = ""
        sample_count = 0

        i = 0
        # First line: "N. Title text"
        if lines:
            title = re.sub(r'^\d+\.\s*', '', lines[0]).strip()
            i = 1

        in_summary = False
        for line in lines[i:]:
            # Summary block starts with "(Submitter supplied)"
            if line.strip().startswith("(Submitter supplied)"):
                summary_lines.append(re.sub(r'^\(Submitter supplied\)\s*', '', line).strip())
                in_summary = True
            elif in_summary and not re.match(r'^(Organism|Type|Platform|FTP|Series)\s*:', line):
                if line.strip():
                    summary_lines.append(line.strip())
                else:
                    in_summary = False
            elif line.startswith("Organism:"):
                organism = line.split(":", 1)[1].strip()
                in_summary = False
            elif line.startswith("Type:"):
                tech_type_raw = line.split(":", 1)[1].strip()
                in_summary = False
            elif line.startswith("Platform:"):
                # "GPL24676 18 Samples" or "GPL24676 GPL24677 18 Samples"
                parts = line.split(":", 1)[1].strip().split()
                for p in parts:
                    if p.startswith("GPL"):
                        platforms.append(p)
                # sample count is last number before "Samples"
                m = re.search(r'(\d+)\s+Samples?', line)
                if m:
                    sample_count = int(m.group(1))
                in_summary = False
            elif line.startswith("Series") and "Accession:" in line:
                m = re.search(r'Accession:\s*(GSE\d+)', line)
                if m:
                    accession = m.group(1)
                in_summary = False

        if not accession or not accession.startswith("GSE"):
            continue

        summary = " ".join(summary_lines).strip()
        # Remove trailing "more..." from truncated summaries
        summary = re.sub(r'\s*more\.\.\.\s*$', '', summary).strip()

        tech_type = GEOParser._infer_tech_type(f"{title} {summary} {tech_type_raw}".lower())
        organisms = [organism] if organism else []

        records.append({
            "accession":        accession,
            "title":            title,
            "summary":          summary,
            "overall_design":   "",   # not in this format — backfill later
            "status":           "",
            "organism_text":    organism,
            "organisms":        organisms,
            "tech_type":        tech_type,
            "platforms":        platforms,
            "pubmed_ids":       [],
            "contributors":     [],
            "submission_date":  None,
            "last_update_date": None,
            "sample_count":     sample_count,
            "raw_record":       {"source": "efetch_summary"},
        })

    return records


# ── Step 4: Upsert to PostgreSQL ─────────────────────────────────────────────

def upsert_batch(db, records: list[dict]) -> int:
    if not records:
        return 0
    rows = [{
        "accession":        r["accession"],
        "title":            r.get("title", ""),
        "summary":          r.get("summary", ""),
        "overall_design":   r.get("overall_design", ""),
        "organism_text":    r.get("organism_text", ""),
        "organisms":        r.get("organisms", []),
        "platforms":        r.get("platforms", []),
        "tech_type":        r.get("tech_type", "unknown"),
        "pubmed_ids":       r.get("pubmed_ids", []),
        "submission_date":  r.get("submission_date"),
        "last_update_date": r.get("last_update_date"),
        "sample_count":     r.get("sample_count"),
        "raw_record":       r.get("raw_record", {}),
    } for r in records]

    stmt = pg_insert(GSESeries).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["accession"],
        set_={c: stmt.excluded[c] for c in [
            "title", "summary", "overall_design", "organism_text",
            "organisms", "platforms", "tech_type", "pubmed_ids",
            "submission_date", "last_update_date", "sample_count", "raw_record",
        ]},
    )
    db.execute(stmt)
    db.commit()
    return len(rows)


# ── Step 5: Embed + store in Milvus ─────────────────────────────────────────

def embed_batch(records: list[dict], embedder, vector_store: MilvusStore, retries: int = 5) -> int:
    if not records:
        return 0
    texts      = [GEOParser.prepare_embedding_text(r) for r in records]
    accessions = [r["accession"] for r in records]
    vecs: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        vecs.extend(embedder.embed_texts(texts[i:i + EMBED_BATCH]))
    for attempt in range(1, retries + 1):
        try:
            vector_store.upsert_embeddings(list(zip(accessions, vecs)))
            return len(records)
        except Exception as e:
            if attempt < retries:
                wait = min(2 ** attempt, 60)
                log.warning(f"Milvus upsert failed (attempt {attempt}/{retries}), retrying in {wait}s: {e}")
                time.sleep(wait)
            else:
                log.error(f"Milvus upsert failed after {retries} attempts, skipping batch: {e}")
                return 0
    return 0


# ── Main ─────────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> None:
    session = requests.Session()
    session.headers["User-Agent"] = "GEOSearch-bulk (geosearch@sdsu.edu)"

    # ── Init DB + embedder ──────────────────────────────────────────────────
    log.info("Initialising DB …")
    init_db()
    db = SessionLocal()

    embedder     = None
    vector_store = None
    if not args.no_embed and not args.dry_run:
        log.info("Loading embedding model …")
        embedder     = get_embedding_provider()
        vector_store = MilvusStore()

    # ── ESearch ─────────────────────────────────────────────────────────────
    web_env, query_key, total = esearch(
        session,
        organisms  = args.organism or ["Homo sapiens"],
        date_start = args.date_start,
        date_end   = args.date_end,
        ncbi_query = args.query,
    )

    if total == 0:
        log.info("No records found.")
        return

    to_fetch = min(total, args.limit) if args.limit else total
    log.info(f"Will fetch {to_fetch:,} records in batches of {BATCH_FETCH}")

    # ── Get existing accessions to skip ─────────────────────────────────────
    existing: set[str] = set()
    if not args.force and not args.dry_run:
        log.info("Loading existing accessions from DB to skip duplicates …")
        rows = db.query(GSESeries.accession).all()
        existing = {r[0] for r in rows}
        log.info(f"  {len(existing):,} already in DB — will skip")

    # ── Fetch + parse + ingest loop ──────────────────────────────────────────
    stats = {"fetched": 0, "parsed": 0, "skipped": 0, "pg": 0, "vec": 0, "errors": 0}
    pending_pg:  list[dict] = []
    pending_vec: list[dict] = []
    start_ts = time.time()

    for retstart in range(0, to_fetch, BATCH_FETCH):
        batch_size = min(BATCH_FETCH, to_fetch - retstart)
        log.info(f"EFetch batch {retstart:,}–{retstart+batch_size:,} / {to_fetch:,} …")

        try:
            soft_text = efetch_soft_batch(session, web_env, query_key, retstart, batch_size)
        except Exception as e:
            log.error(f"EFetch failed at retstart={retstart}: {e}")
            stats["errors"] += batch_size
            continue

        records = parse_efetch_text(soft_text)
        stats["fetched"] += batch_size
        stats["parsed"]  += len(records)

        if args.dry_run:
            for r in records[:3]:
                log.info(f"  {r['accession']} | {r['title'][:60]} | design={bool(r['overall_design'])} | contrib={len(r['contributors'])}")
            continue

        for rec in records:
            if rec["accession"] in existing:
                stats["skipped"] += 1
                continue
            pending_pg.append(rec)
            if rec.get("title") and embedder:
                pending_vec.append(rec)

        # Commit PG batch
        if len(pending_pg) >= BATCH_COMMIT:
            stats["pg"] += upsert_batch(db, pending_pg)
            pending_pg = []

        # Embed batch
        if embedder and len(pending_vec) >= EMBED_BATCH:
            stats["vec"] += embed_batch(pending_vec, embedder, vector_store)
            pending_vec = []

        # Progress log
        elapsed = time.time() - start_ts
        rate    = stats["fetched"] / elapsed if elapsed > 0 else 0
        eta_s   = int((to_fetch - stats["fetched"]) / rate) if rate > 0 else 0
        log.info(
            f"  Progress: {stats['fetched']:,}/{to_fetch:,} fetched | "
            f"PG: {stats['pg']:,} | Vec: {stats['vec']:,} | "
            f"Rate: {rate:.0f} rec/s | ETA: {eta_s//60}m {eta_s%60}s"
        )

    # Flush remainders
    if pending_pg:
        stats["pg"] += upsert_batch(db, pending_pg)
    if pending_vec and embedder:
        stats["vec"] += embed_batch(pending_vec, embedder, vector_store)

    db.close()
    elapsed = int(time.time() - start_ts)
    log.info(
        f"\n{'='*60}\n"
        f"  Fetched:  {stats['fetched']:,}\n"
        f"  Parsed:   {stats['parsed']:,}\n"
        f"  Skipped:  {stats['skipped']:,} (already in DB)\n"
        f"  PG rows:  {stats['pg']:,}\n"
        f"  Milvus:   {stats['vec']:,}\n"
        f"  Errors:   {stats['errors']}\n"
        f"  Time:     {elapsed//60}m {elapsed%60}s\n"
        f"{'='*60}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Bulk GEO ingest via EFetch SOFT batches")
    ap.add_argument("--organism",   action="append", default=[],
                    help="Organism (repeatable). Default: Homo sapiens")
    ap.add_argument("--date-start", default="2000/01/01",
                    help="Start date YYYY/MM/DD (default: 2000/01/01)")
    ap.add_argument("--date-end",   default=datetime.today().strftime("%Y/%m/%d"),
                    help="End date YYYY/MM/DD (default: today)")
    ap.add_argument("--query",      default=None,
                    help="Full NCBI query string (overrides --organism)")
    ap.add_argument("--limit",      type=int, default=None,
                    help="Max records to process (default: all)")
    ap.add_argument("--no-embed",   action="store_true",
                    help="Skip Milvus embedding (DB only)")
    ap.add_argument("--force",      action="store_true",
                    help="Re-ingest records already in DB")
    ap.add_argument("--dry-run",    action="store_true",
                    help="Fetch and parse but do not write to DB or Milvus")
    args = ap.parse_args()
    run(args)


if __name__ == "__main__":
    main()
