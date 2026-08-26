"""
Re-embed all GSE corpus records using the configured embedding model.
Run after changing EMBEDDING_MODEL in .env.

Usage:
    python scripts/reembed_all.py
"""
import os
import sys
import time

# Must be set before importing project modules so config picks them up
os.environ.setdefault("EMBEDDING_MODEL", "NeuML/pubmedbert-base-embeddings")
os.environ.setdefault("EMBEDDING_DIMENSION", "768")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from db.session import SessionLocal
from db.models import GSESeries
from geo_ingest.parser import GEOParser
from vector.milvus_store import MilvusStore
from vector.embeddings import get_embedding_provider

BATCH_SIZE = 256
LOG_EVERY = 256  # log every batch so progress is visible even on small corpora


def main():
    print(f"Embedding model: {os.environ['EMBEDDING_MODEL']}", flush=True)

    print("Loading provider...", flush=True)
    provider = get_embedding_provider()

    print("Connecting to Milvus...", flush=True)
    store = MilvusStore()
    parser = GEOParser()

    db = SessionLocal()
    total = db.query(GSESeries).count()
    print(f"Total records to re-embed: {total:,}", flush=True)

    # Pre-load all MeSH tags into memory as accession → [preferred_name, ...]
    # This avoids N+1 queries during the embedding loop.
    print("Loading MeSH tags...", flush=True)
    mesh_rows = db.execute(text("""
        SELECT gm.accession, mt.preferred_name
        FROM gse_mesh gm
        JOIN mesh_term mt ON gm.mesh_id = mt.mesh_id
    """)).fetchall()
    mesh_map: dict[str, list[str]] = {}
    for acc, name in mesh_rows:
        mesh_map.setdefault(acc, []).append(name)
    print(f"Loaded MeSH tags for {len(mesh_map):,} datasets.", flush=True)

    done = 0
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

        embeddings = provider.embed_texts(texts)
        pairs = list(zip(accessions, embeddings))
        store.upsert_embeddings(pairs)
        done += len(pairs)
        last_accession = batch[-1].accession

        if done % LOG_EVERY == 0 or done == total:
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

    print("Flushing Milvus collection...", flush=True)
    store.collection.flush()
    print("Flush complete.", flush=True)

    elapsed = time.time() - t_start
    print(f"Re-embedding complete. {done:,} records in {elapsed/60:.1f} min.", flush=True)


if __name__ == "__main__":
    main()
