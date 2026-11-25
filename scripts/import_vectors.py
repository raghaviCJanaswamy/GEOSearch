"""Import vectors from numpy file into Milvus."""
import numpy as np
from pymilvus import connections, utility
from vector.milvus_store import MilvusStore

connections.connect(host="milvus", port=19530)

data = np.load("/tmp/geo_vectors.npz", allow_pickle=True)
vectors = data["vectors"]
accessions = data["accessions"].tolist()
print(f"Loaded {len(accessions):,} vectors, shape={vectors.shape}")

store = MilvusStore()
existing = store.count()
if existing > 0:
    print(f"Collection already has {existing:,} vectors — skipping.")
else:
    batch = 1000
    inserted = 0
    for i in range(0, len(accessions), batch):
        batch_acc = accessions[i:i+batch]
        batch_vec = vectors[i:i+batch].tolist()
        store.collection.insert([batch_acc, batch_vec])
        inserted += len(batch_acc)
        if inserted % 10000 == 0:
            print(f"  inserted {inserted:,}/{len(accessions):,}")

    store.collection.flush()
    store.collection.create_index(
        field_name="embedding",
        index_params={"index_type": "IVF_FLAT", "metric_type": "IP", "params": {"nlist": 512}},
    )
    store.collection.load()
    print(f"Done — {store.count():,} vectors in collection")
