"""
Download Homo sapiens GEO Series records with full metadata and ingest to
PostgreSQL + Milvus.

Fields captured:
  status, title, organism, summary, overall_design, contributors, citations

Usage:
    python scripts/fetch_geo_homo_sapiens.py \
        --date-start 2020/01/01 --date-end 2024/12/31 \
        --batch-size 500 --workers 6 --rate-limit 5.0

Environment:
    POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD
    NCBI_API_KEY   (optional — raises NCBI rate limit to 10 req/s)
    NCBI_EMAIL     (required by NCBI guidelines)
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

import requests
from sqlalchemy.dialects.postgresql import insert as pg_insert
from tqdm import tqdm

# ── make project root importable ───────────────────────────────────────────
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

# ── NCBI constants ─────────────────────────────────────────────────────────
ESEARCH_URL  = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
GEO_TEXT_URL = "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi"
BATCH_COMMIT = 100          # postgres commit size
EMBED_BATCH  = 64           # embedding batch size

# NCBI rate limits: 3 req/s without API key, 10 req/s with key
# We sleep conservatively to avoid 429s
_NCBI_MIN_INTERVAL = 0.4    # seconds between NCBI API calls (safe at 3/s)


# ── NCBI helpers ───────────────────────────────────────────────────────────

def _ncbi_params(extra: dict) -> dict:
    global _NCBI_MIN_INTERVAL
    p = {
        "email": os.getenv("NCBI_EMAIL", settings.ncbi_email or "geosearch@example.com"),
        "tool":  "GEOSearch-fetcher",
    }
    api_key = os.getenv("NCBI_API_KEY", settings.ncbi_api_key or "")
    if api_key:
        p["api_key"] = api_key
        _NCBI_MIN_INTERVAL = 0.11   # 10/s with API key
    p.update(extra)
    return p


def _ncbi_get(session: requests.Session, url: str, params: dict, retries: int = 5) -> requests.Response:
    """Rate-limited NCBI GET with retry on 429."""
    for attempt in range(retries):
        time.sleep(_NCBI_MIN_INTERVAL)
        try:
            r = session.get(url, params=params, timeout=30)
            if r.status_code == 429:
                wait = 2 ** attempt
                log.warning(f"429 Too Many Requests — waiting {wait}s (attempt {attempt+1}/{retries})")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r
        except requests.exceptions.RequestException as e:
            if attempt == retries - 1:
                raise
            log.warning(f"Request error: {e} — retrying in {2**attempt}s")
            time.sleep(2 ** attempt)
    raise RuntimeError("Max retries exceeded")


def search_geo(date_start: str, date_end: str, retmax: int = 100_000,
               organisms: list[str] | None = None,
               ncbi_query: str | None = None,
               session: requests.Session | None = None) -> list[str]:
    """
    Return NCBI GDS UIDs for GSE records matching the given filters.
    Uses ESearch with usehistory + WebEnv for large result sets.
    """
    sess = session or requests.Session()

    if ncbi_query:
        # Full query passed in from UI — just add date range
        query = f'({ncbi_query}) AND ("{date_start}"[PDAT] : "{date_end}"[PDAT])'
    else:
        orgs = organisms or ["Homo sapiens"]
        org_terms = " OR ".join(f'"{o}"[Organism]' for o in orgs)
        query = (
            f'({org_terms}) AND gse[Entry Type]'
            f' AND ("{date_start}"[PDAT] : "{date_end}"[PDAT])'
        )
    log.info(f"ESearch query: {query}")

    # First call — get total count and WebEnv
    r = _ncbi_get(sess, ESEARCH_URL, _ncbi_params({
        "db": "gds", "term": query,
        "retmax": 0, "retmode": "json", "usehistory": "y",
    }))
    data = r.json()["esearchresult"]
    total = int(data["count"])
    web_env = data["webenv"]
    query_key = data["querykey"]
    log.info(f"Total records found: {total:,}")

    # Page through results in chunks of 500 (safe batch size)
    all_ids: list[str] = []
    page_size = 500
    for start in range(0, min(total, retmax), page_size):
        r = _ncbi_get(sess, ESEARCH_URL, _ncbi_params({
            "db": "gds", "WebEnv": web_env, "query_key": query_key,
            "retstart": start, "retmax": min(page_size, retmax - start),
            "retmode": "json",
        }))
        ids = r.json()["esearchresult"].get("idlist", [])
        all_ids.extend(ids)
        log.info(f"  UIDs fetched: {len(all_ids):,} / {min(total, retmax):,}")

    return all_ids


# keep old name as alias for backwards compat
search_homo_sapiens = search_geo


# ── GEO SOFT text fetch + parser ───────────────────────────────────────────

def fetch_geo_soft(accession: str, session: requests.Session, rate_limit: float) -> dict[str, Any]:
    """
    Fetch SOFT text for one GSE and return a metadata dict with all fields.
    """
    time.sleep(1.0 / rate_limit)
    try:
        r = session.get(
            GEO_TEXT_URL,
            params={"acc": accession, "targ": "self", "form": "text", "view": "brief"},
            timeout=30,
        )
        r.raise_for_status()
    except Exception as e:
        return {"accession": accession, "error": str(e)}

    return _parse_soft_text(accession, r.text)


def _parse_soft_text(accession: str, text: str) -> dict[str, Any]:
    """Parse !Series_* lines from GEO SOFT text format."""
    def _collect(tag: str) -> list[str]:
        return re.findall(rf"^!Series_{tag}\s*=\s*(.+)$", text, re.MULTILINE)

    def _first(tag: str) -> str:
        vals = _collect(tag)
        return vals[0].strip() if vals else ""

    def _multi(tag: str) -> list[str]:
        return [v.strip() for v in _collect(tag) if v.strip()]

    # Contributors: "Lastname,Firstname,Middle" per line
    raw_contributors = _multi("contributor")
    contributors = []
    for c in raw_contributors:
        parts = [p.strip() for p in c.split(",") if p.strip()]
        contributors.append(" ".join(reversed(parts)) if len(parts) >= 2 else c)

    # Citations: pubmed ids
    pubmed_ids = _multi("pubmed_id")

    # Organisms (may be multiple lines)
    organisms_raw = _multi("organism")
    organisms = list({o.strip() for o in organisms_raw if o.strip()})

    return {
        "accession": accession,
        "status":         _first("status"),
        "title":          _first("title"),
        "summary":        " ".join(_multi("summary")),
        "overall_design": " ".join(_multi("overall_design")),
        "organism_text":  "; ".join(organisms_raw),
        "organisms":      organisms,
        "tech_type":      GEOParser._infer_tech_type(
            f"{_first('title')} {' '.join(_multi('summary'))} {' '.join(_multi('overall_design'))}"
        ),
        "contributors":   contributors,
        "pubmed_ids":     pubmed_ids,
        "submission_date": _first("submission_date"),
        "last_update_date": _first("last_update_date"),
        "platforms":      _multi("platform_id"),
        "sample_count":   len(_multi("sample_id")),
        "raw_record":     {"source": "soft_text"},
    }


# ── UID → GSE accession via ESummary ───────────────────────────────────────

def uids_to_accessions(uids: list[str], session: requests.Session) -> dict[str, str]:
    """Map NCBI GDS UIDs to GSE accessions via ESummary (batched, rate-limited)."""
    mapping: dict[str, str] = {}
    batch_size = 100   # smaller batch = less chance of 429
    total = len(uids)
    for i in range(0, total, batch_size):
        batch = uids[i:i + batch_size]
        r = _ncbi_get(session, ESUMMARY_URL, _ncbi_params({
            "db": "gds", "id": ",".join(batch), "retmode": "json",
        }))
        result = r.json().get("result", {})
        for uid, rec in result.items():
            if uid == "uids":
                continue
            acc = rec.get("accession", "")
            if acc.startswith("GSE"):
                mapping[uid] = acc
        log.info(f"  Accessions resolved: {len(mapping):,} / {min(i + batch_size, total):,}")
    return mapping


# ── DB upsert ──────────────────────────────────────────────────────────────

def upsert_records(db, records: list[dict[str, Any]]) -> int:
    """Upsert a batch of parsed records into gse_series table."""
    if not records:
        return 0

    rows = []
    for r in records:
        sub_date = GEOParser._parse_date(r.get("submission_date"))
        upd_date  = GEOParser._parse_date(r.get("last_update_date"))
        rows.append({
            "accession":       r["accession"],
            "title":           r.get("title", ""),
            "summary":         r.get("summary", ""),
            "overall_design":  r.get("overall_design", ""),
            "organism_text":   r.get("organism_text", ""),
            "organisms":       r.get("organisms", []),
            "platforms":       r.get("platforms", []),
            "tech_type":       r.get("tech_type", "unknown"),
            "pubmed_ids":      r.get("pubmed_ids", []),
            "submission_date": sub_date,
            "last_update_date": upd_date,
            "sample_count":    r.get("sample_count"),
            "raw_record":      {
                **r.get("raw_record", {}),
                "status":       r.get("status", ""),
                "contributors": r.get("contributors", []),
            },
        })

    stmt = pg_insert(GSESeries).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["accession"],
        set_={
            "title":           stmt.excluded.title,
            "summary":         stmt.excluded.summary,
            "overall_design":  stmt.excluded.overall_design,
            "organism_text":   stmt.excluded.organism_text,
            "organisms":       stmt.excluded.organisms,
            "platforms":       stmt.excluded.platforms,
            "tech_type":       stmt.excluded.tech_type,
            "pubmed_ids":      stmt.excluded.pubmed_ids,
            "submission_date": stmt.excluded.submission_date,
            "last_update_date": stmt.excluded.last_update_date,
            "sample_count":    stmt.excluded.sample_count,
            "raw_record":      stmt.excluded.raw_record,
        },
    )
    db.execute(stmt)
    db.commit()
    return len(rows)


# ── Milvus embedding ────────────────────────────────────────────────────────

def embed_and_store(records: list[dict[str, Any]], vector_store: MilvusStore, embedder) -> int:
    """Generate embeddings and upsert into Milvus."""
    if not records:
        return 0

    texts = [GEOParser.prepare_embedding_text(r) for r in records]
    accessions = [r["accession"] for r in records]

    # Batch embed
    all_vecs: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        all_vecs.extend(embedder.embed_texts(texts[i:i + EMBED_BATCH]))

    # upsert_embeddings expects list of (accession, vector) tuples
    vector_store.upsert_embeddings(list(zip(accessions, all_vecs)))
    return len(records)


# ── Main pipeline ───────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> None:
    # Shared HTTP session for all NCBI calls
    http = requests.Session()
    http.headers.update({"User-Agent": "GEOSearch-fetcher (geosearch@example.com)"})

    # Trigger _ncbi_params once so API-key interval is set before any requests
    _ncbi_params({})

    log.info("Initialising DB …")
    init_db()
    db = SessionLocal()

    log.info("Loading embedding model …")
    embedder = get_embedding_provider()
    vector_store = MilvusStore()

    # 1. Search NCBI for all matching UIDs
    uids = search_geo(
        args.date_start, args.date_end,
        retmax=args.max_records,
        organisms=args.organism or ["Homo sapiens"],
        ncbi_query=getattr(args, "query", None),
        session=http,
    )
    log.info(f"UIDs to process: {len(uids):,}")

    # 2. Resolve UIDs → GSE accessions
    log.info("Resolving UIDs to GSE accessions …")
    uid_to_acc = uids_to_accessions(uids, http)
    accessions = list(uid_to_acc.values())
    log.info(f"GSE accessions resolved: {len(accessions):,}")

    # 3. Skip already-ingested (unless --force)
    if not args.force:
        existing = {
            row[0] for row in
            db.query(GSESeries.accession)
              .filter(GSESeries.accession.in_(accessions))
              .all()
        }
        accessions = [a for a in accessions if a not in existing]
        log.info(f"New / to-update: {len(accessions):,}  (skipped {len(uid_to_acc) - len(accessions):,} existing)")

    if not accessions:
        log.info("Nothing to ingest.")
        return

    # 4. Fetch SOFT text concurrently and ingest
    pending_pg:  list[dict] = []
    pending_vec: list[dict] = []
    total_pg = total_vec = 0
    errors = 0

    def _fetch(acc: str) -> dict:
        return fetch_geo_soft(acc, http, args.rate_limit)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_fetch, acc): acc for acc in accessions}
        with tqdm(total=len(accessions), desc="Fetching & ingesting", unit="GSE") as bar:
            for future in as_completed(futures):
                rec = future.result()
                bar.update(1)

                if "error" in rec:
                    log.warning(f"{rec['accession']}: {rec['error']}")
                    errors += 1
                    continue

                pending_pg.append(rec)
                if rec.get("title"):           # only embed if we have content
                    pending_vec.append(rec)

                # Commit to Postgres in batches
                if len(pending_pg) >= BATCH_COMMIT:
                    total_pg += upsert_records(db, pending_pg)
                    pending_pg = []

                # Embed in batches
                if len(pending_vec) >= EMBED_BATCH:
                    total_vec += embed_and_store(pending_vec, vector_store, embedder)
                    pending_vec = []

    # Flush remainders
    if pending_pg:
        total_pg += upsert_records(db, pending_pg)
    if pending_vec:
        total_vec += embed_and_store(pending_vec, vector_store, embedder)

    db.close()
    log.info(
        f"Done.  PostgreSQL: {total_pg:,} upserted | "
        f"Milvus: {total_vec:,} embedded | "
        f"Errors: {errors}"
    )


# ── CLI ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch Homo sapiens GEO Series and ingest to PostgreSQL + Milvus"
    )
    parser.add_argument("--date-start", default="2000/01/01",
                        help="Start date YYYY/MM/DD (default: 2000/01/01)")
    parser.add_argument("--date-end",   default=datetime.today().strftime("%Y/%m/%d"),
                        help="End date   YYYY/MM/DD (default: today)")
    parser.add_argument("--max-records", type=int, default=200_000,
                        help="Maximum records to fetch (default: 200 000)")
    parser.add_argument("--workers",    type=int, default=6,
                        help="Concurrent fetch workers (default: 6)")
    parser.add_argument("--rate-limit", type=float, default=5.0,
                        help="Max requests/sec per worker (default: 5.0)")
    parser.add_argument("--organism",   action="append", default=[],
                        help="Organism filter (repeatable). Default: Homo sapiens. "
                             "E.g. --organism 'Mus musculus' --organism 'Homo sapiens'")
    parser.add_argument("--query",      default=None,
                        help="Full NCBI query string (overrides --organism if provided)")
    parser.add_argument("--force", action="store_true",
                        help="Re-ingest records that already exist in DB")
    args = parser.parse_args()

    run(args)


if __name__ == "__main__":
    main()
