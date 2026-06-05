"""
Ingest missing GSE records from NCBI E-utilities API.

Reads a list of GSE accessions (one per line) and for each:
  1. Fetches base metadata via NCBI ESummary
  2. Fetches overall_design from NCBI SOFT family file (HTTPS)
  3. Upserts the record into PostgreSQL
  4. Generates and upserts Milvus embedding

Usage:
    # Run locally against Docker Compose DB:
    python scripts/ingest_missing_gse.py compare_GSE_NCBI/still_missing_from_geosearch.txt

    # Inside Docker:
    docker exec geosearch-app python scripts/ingest_missing_gse.py \
        compare_GSE_NCBI/still_missing_from_geosearch.txt

    # Dry-run (fetch + parse, no DB/Milvus writes):
    python scripts/ingest_missing_gse.py compare_GSE_NCBI/still_missing_from_geosearch.txt --dry-run

    # Re-process even if already in DB (re-fetches overall_design):
    python scripts/ingest_missing_gse.py compare_GSE_NCBI/still_missing_from_geosearch.txt --force
"""
import argparse
import gzip
import io
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import get_db, init_db
from db.models import GSESeries
from geo_ingest.parser import GEOParser
from vector.embeddings import get_embedding_provider
from vector.milvus_store import MilvusStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HTTP session
# ---------------------------------------------------------------------------

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "GEOSearch/1.0 ingest_missing_gse"})

NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# ---------------------------------------------------------------------------
# NCBI helpers
# ---------------------------------------------------------------------------

def _ncbi_get(endpoint: str, params: dict, retries: int = 3) -> dict | None:
    """GET an NCBI E-utilities endpoint and return parsed JSON."""
    url = f"{NCBI_BASE}/{endpoint}"
    for attempt in range(1, retries + 1):
        try:
            time.sleep(0.35)  # ~3 req/s
            resp = SESSION.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt < retries:
                wait = 2 ** attempt
                logger.warning(f"NCBI {endpoint} attempt {attempt} failed ({e}), retry in {wait}s")
                time.sleep(wait)
            else:
                logger.error(f"NCBI {endpoint} failed after {retries} attempts: {e}")
    return None


def fetch_esummary(accession: str) -> dict | None:
    """Return ESummary dict for a single GSE accession, or None on failure."""
    # Step 1: resolve accession → UID
    search = _ncbi_get("esearch.fcgi", {
        "db": "gds",
        "term": f"{accession}[Accession]",
        "retmode": "json",
    })
    if not search:
        return None
    id_list = search.get("esearchresult", {}).get("idlist", [])
    if not id_list:
        logger.warning(f"{accession}: not found in NCBI ESearch")
        return None

    uid = id_list[0]

    # Step 2: fetch summary
    summary_resp = _ncbi_get("esummary.fcgi", {
        "db": "gds",
        "id": uid,
        "retmode": "json",
    })
    if not summary_resp:
        return None

    result = summary_resp.get("result", {})
    return result.get(uid)


def fetch_overall_design_soft(accession: str, retries: int = 3) -> str | None:
    """Download the SOFT family file and extract overall_design."""
    stub = accession[:-3] + "nnn"
    url = (
        f"https://ftp.ncbi.nlm.nih.gov/geo/series/{stub}/{accession}"
        f"/soft/{accession}_family.soft.gz"
    )
    for attempt in range(1, retries + 1):
        try:
            time.sleep(0.35)
            resp = SESSION.get(url, timeout=60, stream=True)
            if resp.status_code == 404:
                logger.debug(f"{accession}: SOFT file not found (404)")
                return None
            resp.raise_for_status()

            buf = io.BytesIO(resp.content)
            with gzip.open(buf, "rt", encoding="utf-8", errors="replace") as f:
                lines = []
                for line in f:
                    if line.startswith("!Series_overall_design"):
                        value = line.split("=", 1)[1].strip()
                        lines.append(value)
                    elif lines and line.startswith("!"):
                        break
                    elif lines:
                        lines.append(line.strip())

            if lines:
                return " ".join(lines).strip()
            return None

        except Exception as e:
            if attempt < retries:
                wait = 2 ** attempt
                logger.warning(f"{accession}: SOFT attempt {attempt} failed ({e}), retry in {wait}s")
                time.sleep(wait)
            else:
                logger.error(f"{accession}: SOFT fetch failed: {e}")
    return None


# ---------------------------------------------------------------------------
# Build raw metadata dict from ESummary + overall_design
# ---------------------------------------------------------------------------

def build_raw_metadata(accession: str, summary: dict, overall_design: str) -> dict:
    return {
        "accession": accession,
        "title": summary.get("title", ""),
        "summary": summary.get("summary", ""),
        "overall_design": overall_design,
        "type": summary.get("gdstype", ""),
        "platform_ids": [summary.get("gpl", "")] if summary.get("gpl") else [],
        "sample_ids": [],
        "pubmed_ids": [],
        "taxon": summary.get("taxon", ""),
        "entrez_date": summary.get("pdat", ""),
        "submission_date": summary.get("pdat", ""),
        "n_samples": summary.get("n_samples", ""),
        "organisms": [summary.get("taxon", "")] if summary.get("taxon") else [],
    }


# ---------------------------------------------------------------------------
# Main ingest logic
# ---------------------------------------------------------------------------

def ingest_missing(
    accessions: list[str],
    dry_run: bool = False,
    force: bool = False,
    embed_chunk: int = 50,
) -> None:
    init_db()
    db = next(get_db())
    parser = GEOParser()

    if not dry_run:
        embedding_provider = get_embedding_provider()
        vector_store = MilvusStore()

    # Determine which accessions to skip (already in DB with overall_design)
    if not force:
        existing_full = {
            row[0]
            for row in db.query(GSESeries.accession).filter(
                GSESeries.accession.in_(accessions),
                GSESeries.overall_design != None,
                GSESeries.overall_design != "",
            ).all()
        }
        to_process = [a for a in accessions if a not in existing_full]
        skipped_pre = len(accessions) - len(to_process)
        if skipped_pre:
            logger.info(f"Skipping {skipped_pre} accessions that already have overall_design")
    else:
        to_process = list(accessions)

    total = len(to_process)
    logger.info(f"Processing {total} accessions")

    success = 0
    errors = 0
    not_found = 0
    parsed_for_embed: list[tuple[str, dict]] = []

    for i, accession in enumerate(to_process, 1):
        logger.info(f"[{i}/{total}] {accession}")

        # --- Fetch ESummary ---
        summary = fetch_esummary(accession)
        if not summary:
            logger.warning(f"  {accession}: no ESummary data, skipping")
            not_found += 1
            continue

        # --- Fetch overall_design from SOFT ---
        overall_design = fetch_overall_design_soft(accession) or ""
        if overall_design:
            logger.info(f"  overall_design: {overall_design[:80]}...")
        else:
            logger.debug(f"  overall_design: (not found)")

        raw = build_raw_metadata(accession, summary, overall_design)
        parsed = parser.parse_gse_metadata(raw)
        if not parsed:
            logger.warning(f"  {accession}: parser returned empty result")
            errors += 1
            continue

        if dry_run:
            logger.info(f"  [dry-run] would upsert: {parsed['title'][:60]}")
            success += 1
            continue

        # --- Upsert into PostgreSQL ---
        try:
            db.merge(GSESeries(**parsed))
            db.commit()
            parsed_for_embed.append((accession, parsed))
            success += 1
            logger.info(f"  stored in PostgreSQL")
        except Exception as e:
            db.rollback()
            errors += 1
            logger.error(f"  DB upsert failed: {e}")
            continue

        # --- Flush embeddings in chunks ---
        if len(parsed_for_embed) >= embed_chunk:
            _flush_embeddings(parsed_for_embed, parser, embedding_provider, vector_store)
            parsed_for_embed = []

    # Flush remaining embeddings
    if parsed_for_embed and not dry_run:
        _flush_embeddings(parsed_for_embed, parser, embedding_provider, vector_store)

    db.close()

    print(f"\n{'='*55}")
    print(f"Ingest complete")
    print(f"  Processed:  {total}")
    print(f"  Stored:     {success}")
    print(f"  Not found:  {not_found}")
    print(f"  Errors:     {errors}")
    print(f"{'='*55}")


def _flush_embeddings(
    batch: list[tuple[str, dict]],
    parser: GEOParser,
    embedding_provider,
    vector_store: MilvusStore,
) -> None:
    """Generate embeddings for a batch and upsert into Milvus."""
    try:
        texts = [parser.prepare_embedding_text(p) for _, p in batch]
        embeddings = embedding_provider.embed_texts(texts)
        vectors = [
            (acc, emb)
            for (acc, _), emb in zip(batch, embeddings)
            if emb is not None
        ]
        if vectors:
            vector_store.upsert_embeddings(vectors)
            logger.info(f"  Milvus upsert: {len(vectors)} embeddings")
    except Exception as e:
        logger.error(f"  Milvus embedding flush failed: {e}", exc_info=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Ingest missing GSE records via NCBI E-utilities + SOFT files"
    )
    ap.add_argument(
        "file",
        help="Path to text file with one GSE accession per line "
             "(e.g. compare_GSE_NCBI/still_missing_from_geosearch.txt)",
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Fetch and parse but do not write to DB or Milvus",
    )
    ap.add_argument(
        "--force", action="store_true",
        help="Re-process accessions that already have overall_design in DB",
    )
    ap.add_argument(
        "--embed-chunk", type=int, default=50,
        help="Batch size for Milvus embedding upserts (default: 50)",
    )
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    accessions = [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and line.strip().startswith("GSE")
    ]

    if not accessions:
        print("No GSE accessions found in file.", file=sys.stderr)
        sys.exit(1)

    logger.info(f"Loaded {len(accessions)} accessions from {path}")
    ingest_missing(
        accessions,
        dry_run=args.dry_run,
        force=args.force,
        embed_chunk=args.embed_chunk,
    )


if __name__ == "__main__":
    main()
