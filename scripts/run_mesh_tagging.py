"""
CLI script for MeSH auto-tagging all GSE series.
Equivalent to: Ingest → MeSH Tagger tab → Start Tagging

Usage:
    python scripts/run_mesh_tagging.py [--confidence 0.3] [--overwrite]

    --confidence   Minimum match confidence (default: 0.3)
    --overwrite    Re-tag already-tagged records (default: skip)
"""
import sys
import time
import argparse

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))

from db.session import SessionLocal
from db.models import GSESeries, GSEMesh
from mesh.matcher import MeSHMatcher

parser = argparse.ArgumentParser()
parser.add_argument("--confidence", type=float, default=0.3)
parser.add_argument("--overwrite", action="store_true")
args = parser.parse_args()

BATCH = 500

print(f"MeSH tagging — confidence={args.confidence}, overwrite={args.overwrite}", flush=True)

with SessionLocal() as db:
    accessions = [r[0] for r in db.query(GSESeries.accession).all()]
    total = len(accessions)
    print(f"Total records : {total:,}", flush=True)

    if not args.overwrite:
        already = {r[0] for r in db.execute(__import__("sqlalchemy").text(
            "SELECT DISTINCT accession FROM gse_mesh WHERE source='auto'"
        )).fetchall()}
        accessions = [a for a in accessions if a not in already]
        print(f"Already tagged: {len(already):,} — processing {len(accessions):,} remaining", flush=True)

    matcher = MeSHMatcher(db)
    done = 0
    tags = 0
    t0 = time.time()

    for i in range(0, len(accessions), BATCH):
        batch = accessions[i:i + BATCH]
        for acc in batch:
            if args.overwrite:
                db.query(GSEMesh).filter(GSEMesh.accession == acc, GSEMesh.source == "auto").delete()
            for m in matcher.match_gse(acc, args.confidence):
                db.merge(GSEMesh(accession=acc, mesh_id=m["mesh_id"], source="auto", confidence=m["confidence"]))
                tags += 1
        db.commit()
        done = min(i + BATCH, len(accessions))
        elapsed = time.time() - t0
        rate = done / elapsed if elapsed > 0 else 1
        eta = (len(accessions) - done) / rate
        pct = done / len(accessions) * 100 if accessions else 100
        bar = "#" * int(30 * done / len(accessions)) + "-" * (30 - int(30 * done / len(accessions))) if accessions else "#" * 30
        print(f"[{bar}] {pct:5.1f}%  {done:,}/{len(accessions):,}  tags={tags:,}  ETA {eta/60:.1f} min", flush=True)

    print(f"\nDone. {tags:,} MeSH tags applied to {done:,} records in {(time.time()-t0)/60:.1f} min.", flush=True)
