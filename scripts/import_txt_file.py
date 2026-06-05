"""
Import GEO datasets from a plain-text summary file (gds_result_summary.txt)
into PostgreSQL and generate Milvus embeddings.

Usage:
    python scripts/import_txt_file.py data/mesh/gds_result_summary.txt
    python scripts/import_txt_file.py data/mesh/gds_result_summary.txt --no-skip-existing
"""
import argparse
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db import IngestRun, get_db, init_db
from db.models import GSESeries
from geo_ingest.parser import GEOParser
from vector.embeddings import get_embedding_provider
from vector.milvus_store import MilvusStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parser for the flat-text format
# ---------------------------------------------------------------------------

def parse_txt_file(path: str) -> list[dict]:
    """
    Parse gds_result_summary.txt into a list of raw record dicts.

    Each record looks like:
        N. Title
        (Submitter supplied) Summary text...
        Organism:   Homo sapiens
        Type:       Expression profiling by high throughput sequencing
        Platform: GPL24676 27 Samples
        FTP download: GEO (...) ftp://...
        Series      Accession: GSE232419    ID: 200232419
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")

    # Split on record boundaries — blank line followed by a number+period
    blocks = re.split(r"\n(?=\d+\.\s)", text.strip())

    records = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue

        lines = block.splitlines()

        # Title: first line, strip leading "N. "
        title = re.sub(r"^\d+\.\s*", "", lines[0]).strip() if lines else ""

        # Summary: line starting with "(Submitter supplied)"
        summary = ""
        for line in lines[1:]:
            if line.startswith("(Submitter supplied)"):
                summary = re.sub(r"^\(Submitter supplied\)\s*", "", line).strip()
                summary = summary.rstrip(" more...")
                break

        # Organism
        organism = ""
        for line in lines:
            m = re.match(r"Organism:\s*(.+)", line)
            if m:
                organism = m.group(1).strip()
                break

        # Type
        tech_type_raw = ""
        for line in lines:
            m = re.match(r"Type:\s*(.+)", line)
            if m:
                tech_type_raw = m.group(1).strip()
                break

        # Platform + sample count  e.g. "GPL24676 27 Samples"
        platforms = []
        sample_count = None
        for line in lines:
            m = re.match(r"Platform:\s*(.+)", line)
            if m:
                platform_str = m.group(1).strip()
                for part in platform_str.split():
                    if part.startswith("GPL"):
                        gpl_id = re.sub(r"^GPL", "", part)
                        if gpl_id.isdigit():
                            platforms.append(int(gpl_id))
                count_m = re.search(r"(\d+)\s+Samples?", platform_str, re.IGNORECASE)
                if count_m:
                    sample_count = int(count_m.group(1))
                break

        # Accession
        accession = ""
        for line in lines:
            m = re.search(r"Accession:\s*(GSE\d+)", line)
            if m:
                accession = m.group(1).strip()
                break

        if not accession:
            logger.warning(f"Skipping block — no accession found: {title[:60]}")
            continue

        records.append({
            "accession": accession,
            "title": title,
            "summary": summary,
            "overall_design": "",
            "organisms": [organism] if organism else [],
            "tech_type_raw": tech_type_raw,
            "platforms": platforms,
            "sample_count": sample_count,
            "submission_date": None,
            "pubmed_ids": [],
        })

    logger.info(f"Parsed {len(records)} records from {path}")
    return records


# ---------------------------------------------------------------------------
# Main import logic
# ---------------------------------------------------------------------------

def import_records(txt_path: str, skip_existing: bool = True) -> None:
    records = parse_txt_file(txt_path)
    if not records:
        logger.error("No records parsed — check file format.")
        sys.exit(1)

    init_db()

    db = next(get_db())
    parser = GEOParser()
    embedding_provider = get_embedding_provider()
    vector_store = MilvusStore()

    # Create an ingest run entry
    run = IngestRun(
        query=f"txt_import:{Path(txt_path).name}",
        start_time=datetime.now(timezone.utc),
        status="running",
        run_metadata={"source_file": txt_path, "total": len(records)},
    )
    db.add(run)
    db.commit()

    # Filter existing records
    all_accessions = [r["accession"] for r in records]
    skipped = 0
    if skip_existing:
        existing = {
            row[0]
            for row in db.query(GSESeries.accession)
            .filter(GSESeries.accession.in_(all_accessions))
            .all()
        }
        before = len(records)
        records = [r for r in records if r["accession"] not in existing]
        skipped = before - len(records)
        logger.info(f"Skipping {skipped} existing records, processing {len(records)}")

    success = 0
    errors = 0
    parsed_for_embed: list[tuple[str, dict]] = []

    for i, raw in enumerate(records, 1):
        accession = raw["accession"]
        logger.info(f"[{i}/{len(records)}] {accession} — {raw['title'][:60]}")
        try:
            parsed = parser.parse_gse_metadata(raw)
            if not parsed:
                logger.warning(f"  Skipped (empty parse): {accession}")
                errors += 1
                continue

            db.merge(GSESeries(**parsed))
            db.commit()
            parsed_for_embed.append((accession, parsed))
            success += 1
            logger.info(f"  stored")

        except Exception as e:
            logger.error(f"  failed: {e}")
            db.rollback()
            errors += 1

    # Batch generate and upsert embeddings in chunks to stay under gRPC 64MB limit
    EMBED_CHUNK = 500
    if parsed_for_embed:
        logger.info(f"Generating embeddings for {len(parsed_for_embed)} records (chunk={EMBED_CHUNK})...")
        total_stored = 0
        try:
            for chunk_start in range(0, len(parsed_for_embed), EMBED_CHUNK):
                chunk = parsed_for_embed[chunk_start:chunk_start + EMBED_CHUNK]
                texts = [parser.prepare_embedding_text(p) for _, p in chunk]
                embeddings = embedding_provider.embed_texts(texts)
                vectors = [
                    (acc, emb)
                    for (acc, _), emb in zip(chunk, embeddings)
                    if emb is not None
                ]
                if vectors:
                    vector_store.upsert_embeddings(vectors)
                    total_stored += len(vectors)
                    logger.info(f"  Milvus upsert: {total_stored}/{len(parsed_for_embed)}")
            logger.info(f"Stored {total_stored} embeddings in Milvus")
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}", exc_info=True)

    # Finalize run record
    run.end_time = datetime.now(timezone.utc)
    run.total_count = len(records) + skipped
    run.success_count = success
    run.error_count = errors
    run.status = "completed" if errors == 0 else "partial"
    db.commit()
    db.close()

    print(f"\n{'='*50}")
    print(f"Import complete: {success} success, {errors} errors, {skipped} skipped")
    print(f"{'='*50}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Import GEO summary txt into DB + Milvus")
    ap.add_argument("file", help="Path to gds_result_summary.txt")
    ap.add_argument("--no-skip-existing", dest="skip_existing", action="store_false",
                    help="Re-ingest records already in the database")
    ap.set_defaults(skip_existing=True)
    args = ap.parse_args()

    import_records(args.file, skip_existing=args.skip_existing)
