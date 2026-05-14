python3 -c "
from pymilvus import connections, Collection
connections.connect(host='localhost', port='19530')
col = Collection('geo_gse_embeddings')
col.load()
results = col.query(expr='accession != \"\"', output_fields=['accession'], limit=1000)
milvus_accessions = {r['accession'] for r in results}

from db.session import SessionLocal
from db.models import GSESeries
from geo_ingest.parser import GEOParser
from vector.milvus_store import MilvusStore
from vector.embeddings import get_embedding_provider

db = SessionLocal()
all_gse = db.query(GSESeries).all()
missing = [gse for gse in all_gse if gse.accession not in milvus_accessions]
print(f'Re-embedding {len(missing)} records...')

parser = GEOParser()
provider = get_embedding_provider()
store = MilvusStore()

batch_size = 50
for i in range(0, len(missing), batch_size):
    batch = missing[i:i+batch_size]
    texts = []
    for gse in batch:
        parsed = {
            'accession': gse.accession,
            'title': gse.title or '',
            'summary': gse.summary or '',
            'overall_design': gse.overall_design or '',
            'organisms': gse.organisms or [],
            'tech_type': gse.tech_type or '',
        }
        texts.append(parser.prepare_embedding_text(parsed))
    embeddings = provider.embed_texts(texts)
    pairs = [(gse.accession, emb) for gse, emb in zip(batch, embeddings)]
    store.upsert_embeddings(pairs)
    print(f'Batch {i//batch_size + 1}: upserted {len(pairs)} embeddings')

db.close()
print('Done.')
" 2>&1