"""Pre-flight check before re-embedding run."""
import sys
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))

from config import settings
from vector.embeddings import get_embedding_provider
from vector.milvus_store import MilvusStore
from db import SessionLocal, GSESeries
from geo_ingest.parser import GEOParser
from sqlalchemy import text

ok = True

print("=== Config ===")
print(f"  embedding_provider  : {settings.embedding_provider}")
print(f"  embedding_dimension : {settings.embedding_dimension}")
print(f"  azure_endpoint      : {'set' if settings.azure_openai_endpoint else 'MISSING'}")
print(f"  azure_api_key       : {'set' if settings.azure_openai_api_key else 'MISSING'}")
print(f"  azure_deployment    : {'set' if settings.azure_openai_deployment else 'MISSING'}")
print(f"  azure_api_version   : {settings.azure_openai_api_version}")

if not all([settings.azure_openai_endpoint, settings.azure_openai_api_key, settings.azure_openai_deployment]):
    print("  ERROR: one or more Azure config values missing")
    ok = False

print()
print("=== Provider ===")
try:
    p = get_embedding_provider()
    print(f"  type      : {type(p).__name__}")
    print(f"  dimension : {p.get_dimension()}")
except Exception as e:
    print(f"  ERROR: {e}")
    ok = False

print()
print("=== API test (2 texts) ===")
try:
    vecs = p.embed_texts(["breast cancer RNA-seq", "single cell lung adenocarcinoma"])
    print(f"  vectors returned : {len(vecs)}")
    print(f"  dimension        : {len(vecs[0])}")
    print(f"  values look sane : {all(isinstance(v, float) for v in vecs[0][:5])}")
    if len(vecs[0]) != settings.embedding_dimension:
        print(f"  WARNING: vector dim {len(vecs[0])} != config dim {settings.embedding_dimension}")
        ok = False
except Exception as e:
    print(f"  ERROR: {e}")
    ok = False

print()
print("=== Milvus ===")
try:
    store = MilvusStore()
    print(f"  collection : {store.collection_name}")
    print(f"  dimension  : {store.dimension}")
    print(f"  entities   : {store.collection.num_entities:,}")
    if store.dimension != settings.embedding_dimension:
        print(f"  WARNING: Milvus dim {store.dimension} != config dim {settings.embedding_dimension}")
        print(f"  Collection will be dropped and recreated on run.")
except Exception as e:
    print(f"  ERROR: {e}")
    ok = False

print()
print("=== PostgreSQL ===")
try:
    db = SessionLocal()
    total = db.query(GSESeries).count()
    mesh_cnt = db.execute(text("SELECT COUNT(DISTINCT accession) FROM gse_mesh")).scalar()
    print(f"  gse_series rows : {total:,}")
    print(f"  mesh-tagged     : {mesh_cnt:,}")

    gse = db.query(GSESeries).order_by(GSESeries.accession).first()
    mesh_rows = db.execute(
        text("SELECT mt.preferred_name FROM gse_mesh gm JOIN mesh_term mt ON gm.mesh_id = mt.mesh_id WHERE gm.accession = :acc LIMIT 3"),
        {"acc": gse.accession}
    ).fetchall()
    parsed = {
        "accession": gse.accession,
        "title": gse.title or "",
        "summary": gse.summary or "",
        "overall_design": gse.overall_design or "",
        "organisms": gse.organisms or [],
        "tech_type": gse.tech_type or "",
        "mesh_terms": [r[0] for r in mesh_rows],
    }
    parser = GEOParser()
    sample_text = parser.prepare_embedding_text(parsed)
    print(f"  sample accession : {gse.accession}")
    print(f"  text length      : {len(sample_text)} chars / {len(sample_text.split())} words")
    print(f"  preview          : {sample_text[:80]}...")
    db.close()
except Exception as e:
    print(f"  ERROR: {e}")
    ok = False

print()
if ok:
    print("=== All checks passed. Ready to reembed. ===")
else:
    print("=== PREFLIGHT FAILED — fix errors above before running reembed_all.py ===")
    sys.exit(1)
