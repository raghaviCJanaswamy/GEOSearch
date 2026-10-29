"""
Re-embed all GSE corpus records using the configured embedding provider.

Reads EMBEDDING_PROVIDER, EMBEDDING_DIMENSION and provider credentials from .env.

Usage:
    python scripts/reembed_all.py [--resume] [--rebuild]

    (default)  If the collection already has vectors, resumes automatically.
               If the collection is empty or missing, builds from scratch.
    --resume   Explicitly skip records already in Milvus (same as default).
    --rebuild  Drop and recreate the collection from scratch (use when switching
               embedding model/dimension, e.g. PubMedBERT 768 → text-embedding-3-large 3072).
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from db.session import SessionLocal
from db.models import GSESeries
from geo_ingest.parser import GEOParser
from vector.milvus_store import MilvusStore
from vector.embeddings import get_embedding_provider
from config import settings

BATCH_SIZE = 256
LOG_EVERY  = 256
MAX_RETRIES = 5
RETRY_BASE_DELAY = 10  # seconds; doubles on each retry (exponential backoff)


def embed_with_retry(provider, texts: list[str]) -> list[list[float]]:
    """Call provider.embed_texts with exponential backoff on rate-limit errors."""
    delay = RETRY_BASE_DELAY
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return provider.embed_texts(texts)
        except Exception as e:
            msg = str(e).lower()
            is_rate_limit = any(k in msg for k in ("429", "rate limit", "too many requests", "throttl"))
            if is_rate_limit and attempt < MAX_RETRIES:
                print(f"  Rate limit hit — waiting {delay}s (attempt {attempt}/{MAX_RETRIES})", flush=True)
                time.sleep(delay)
                delay = min(delay * 2, 120)
            else:
                raise


def main():
    rebuild = "--rebuild" in sys.argv

    print(f"Embedding provider  : {settings.embedding_provider}", flush=True)
    print(f"Embedding dimension : {settings.embedding_dimension}", flush=True)

    print("Loading provider...", flush=True)
    provider = get_embedding_provider()
    actual_dim = provider.get_dimension()
    print(f"Provider dimension  : {actual_dim}", flush=True)

    print("Connecting to Milvus...", flush=True)
    store = MilvusStore()
    existing = store.count()

    if rebuild:
        print("--rebuild flag set: dropping existing collection to rebuild from scratch...", flush=True)
        store.drop_collection()
        store = MilvusStore()
        print(f"New collection created (dim={store.dimension}).", flush=True)
    elif existing > 0:
        print(f"Resume mode: {existing:,} vectors already in Milvus — skipping those accessions.", flush=True)
    else:
        print("Collection empty — building from scratch.", flush=True)

    parser = GEOParser()
    db = SessionLocal()

    total = db.query(GSESeries).count()
    print(f"Total records       : {total:,}", flush=True)

    print("Loading MeSH tags...", flush=True)
    mesh_rows = db.execute(text("""
        SELECT gm.accession, mt.preferred_name
        FROM gse_mesh gm
        JOIN mesh_term mt ON gm.mesh_id = mt.mesh_id
    """)).fetchall()
    mesh_map: dict[str, list[str]] = {}
    for acc, name in mesh_rows:
        mesh_map.setdefault(acc, []).append(name)
    print(f"MeSH tags loaded for {len(mesh_map):,} datasets.", flush=True)

    done = 0
    skipped = 0
    errors = 0
    t_start = time.time()
    last_accession = ""

    while True:
        batch = (
            db.query(GSESeries)
            .filter(GSESeries.accession > last_accession)
            .order_by(GSESeries.accession)
            .limit(BATCH_SIZE)
            .all()
        )
        if not batch:
            break

        # In resume mode, skip accessions already present in Milvus
        if existing > 0:
            batch_accs = [gse.accession for gse in batch]
            expr = " || ".join(f'accession == "{a}"' for a in batch_accs)
            try:
                hits = store.collection.query(expr=expr, output_fields=["accession"])
                already_done = {h["accession"] for h in hits}
            except Exception:
                already_done = set()
            if already_done:
                skipped += len(already_done)
                batch = [gse for gse in batch if gse.accession not in already_done]
            if not batch:
                last_accession = batch_accs[-1]  # advance cursor past this batch
                continue

        texts, accessions = [], []
        for gse in batch:
            parsed = {
                "accession": gse.accession,
                "title": gse.title or "",
                "summary": gse.summary or "",
                "overall_design": gse.overall_design or "",
                "organisms": gse.organisms or [],
                "tech_type": gse.tech_type or "",
                "mesh_terms": mesh_map.get(gse.accession, []),
            }
            texts.append(parser.prepare_embedding_text(parsed))
            accessions.append(gse.accession)

        try:
            embeddings = embed_with_retry(provider, texts)
            pairs = list(zip(accessions, embeddings))
            store.upsert_embeddings(pairs)
            done += len(pairs)
        except Exception as e:
            print(f"  ERROR on batch ending {batch[-1].accession}: {e}", flush=True)
            errors += 1

        last_accession = batch[-1].accession

        if done % LOG_EVERY == 0 or done >= total - BATCH_SIZE:
            elapsed = time.time() - t_start
            rate = done / elapsed if elapsed > 0 else 0
            eta = (total - done) / rate if rate > 0 else 0
            pct = done / total * 100
            bar_len = 30
            filled = int(bar_len * done / total)
            bar = "#" * filled + "-" * (bar_len - filled)
            print(
                f"[{bar}] {pct:5.1f}%  {done:,}/{total:,}  {rate:.0f} rec/s  ETA {eta/60:.1f} min",
                flush=True,
            )

    db.close()

    elapsed = time.time() - t_start
    print(
        f"\nRe-embedding complete. {done:,} embedded, {skipped:,} skipped (already in Milvus), "
        f"{errors} errors. Total time: {elapsed/60:.1f} min.",
        flush=True,
    )


if __name__ == "__main__":
    main()
