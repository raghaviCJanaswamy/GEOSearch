python3 -c "
from pymilvus import connections, Collection
from db.session import SessionLocal
from db.models import GSESeries

# Milvus count
connections.connect(host='localhost', port='19530')
col = Collection('geo_gse_embeddings')
col.load()
milvus_count = col.num_entities

# Postgres count
db = SessionLocal()
pg_count = db.query(GSESeries).count()
db.close()

print(f'PostgreSQL records : {pg_count}')
print(f'Milvus vectors     : {milvus_count}')
print(f'In sync            : {pg_count == milvus_count}')
if pg_count != milvus_count:
    print(f'Difference         : {abs(pg_count - milvus_count)} missing from {\"Milvus\" if pg_count > milvus_count else \"Postgres\"}')
" 2>&1