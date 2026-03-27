python3 -c "
from pymilvus import connections, Collection
connections.connect(host='localhost', port='19530')
col = Collection('geo_gse_embeddings')
col.load()
print('Entity count:', col.num_entities)
results = col.query(expr='accession != \"\"', output_fields=['accession'], limit=1000)
print('Queryable count:', len(results))
" 2>&1