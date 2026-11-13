"""Export all vectors from Milvus using accession-range pagination with incremental saves."""
import numpy as np
import os
from pymilvus import connections, Collection
from db.session import SessionLocal
from db.models import GSESeries

connections.connect(host="milvus", port=19530)
col = Collection("geo_gse_embeddings")
col.load()

OUT = "/tmp/geo_vectors.npz"
CHECKPOINT = "/tmp/geo_vectors_checkpoint.txt"
BATCH = 500

# Get all accessions from Postgres (sorted)
db = SessionLocal()
all_accs = [r[0] for r in db.query(GSESeries.accession).order_by(GSESeries.accession).all()]
db.close()
print(f"Total accessions in Postgres: {len(all_accs):,}")

# Resume from checkpoint
start_idx = 0
all_accessions, all_vectors = [], []

if os.path.exists(CHECKPOINT):
    with open(CHECKPOINT) as f:
        start_idx = int(f.read().strip())
    if os.path.exists(OUT):
        d = np.load(OUT, allow_pickle=True)
        all_accessions = d["accessions"].tolist()
        all_vectors = d["vectors"].tolist()
    print(f"Resuming from index {start_idx:,}, already have {len(all_accessions):,} vectors")

for i in range(start_idx, len(all_accs), BATCH):
    batch_accs = all_accs[i:i+BATCH]
    acc_list = '","'.join(batch_accs)
    try:
        results = col.query(
            expr=f'accession in ["{acc_list}"]',
            output_fields=["accession", "embedding"],
            timeout=30,
        )
        for r in results:
            all_accessions.append(r["accession"])
            all_vectors.append(r["embedding"])
    except Exception as e:
        print(f"  WARNING: batch {i} failed: {e} — skipping")

    if (i // BATCH) % 20 == 0:
        # Save incrementally every 10K records
        vectors_np = np.array(all_vectors, dtype=np.float32)
        accessions_np = np.array(all_accessions)
        np.savez_compressed(OUT, vectors=vectors_np, accessions=accessions_np)
        with open(CHECKPOINT, "w") as f:
            f.write(str(i + BATCH))
        print(f"  checkpoint: {len(all_accessions):,}/{len(all_accs):,}")

# Final save
vectors_np = np.array(all_vectors, dtype=np.float32)
accessions_np = np.array(all_accessions)
np.savez_compressed(OUT, vectors=vectors_np, accessions=accessions_np)
os.remove(CHECKPOINT) if os.path.exists(CHECKPOINT) else None
print(f"Done — saved {len(all_accessions):,} vectors, shape={vectors_np.shape}")
print(f"File: {OUT}")
