#!/usr/bin/env python3
"""Display database statistics and information."""
import sys
from datetime import datetime

from sqlalchemy import func

from config import settings
from db import GSEMesh, GSESeries, IngestRun, MeshTerm, get_db


def main():
    """Display database information."""
    db = next(get_db())

    print("=" * 60)
    print("GEOSearch Database Statistics")
    print("=" * 60)
    print()

    # Connection info
    print(f"Database: {settings.database_url.split('@')[-1]}")
    print()

    # GSE Series
    gse_count = db.query(func.count(GSESeries.accession)).scalar()
    print(f"GSE Series Records: {gse_count:,}")

    if gse_count > 0:
        # Date range
        min_date, max_date = db.query(
            func.min(GSESeries.submission_date),
            func.max(GSESeries.submission_date),
        ).first()

        if min_date and max_date:
            print(f"  Date range: {min_date.date()} to {max_date.date()}")

        # Top organisms
        print("\n  Top organisms:")
        org_query = (
            db.query(
                func.jsonb_array_elements_text(GSESeries.organisms).label("organism"),
                func.count().label("count"),
            )
            .group_by("organism")
            .order_by(func.count().desc())
            .limit(5)
        )

        for org, count in org_query:
            print(f"    - {org}: {count:,}")

        # Tech types
        print("\n  Technology types:")
        tech_query = (
            db.query(GSESeries.tech_type, func.count())
            .filter(GSESeries.tech_type.isnot(None))
            .group_by(GSESeries.tech_type)
            .order_by(func.count().desc())
            .limit(5)
        )

        for tech, count in tech_query:
            print(f"    - {tech}: {count:,}")

    print()

    # MeSH Terms
    mesh_count = db.query(func.count(MeshTerm.mesh_id)).scalar()
    print(f"MeSH Terms: {mesh_count:,}")

    # MeSH Associations
    mesh_assoc_count = db.query(func.count(GSEMesh.id)).scalar()
    print(f"GSE-MeSH Associations: {mesh_assoc_count:,}")

    if mesh_assoc_count > 0:
        avg_terms = db.query(
            func.count(GSEMesh.mesh_id) / func.count(func.distinct(GSEMesh.accession))
        ).scalar()
        print(f"  Average MeSH terms per dataset: {avg_terms:.1f}")

    print()

    # Ingestion runs
    run_count = db.query(func.count(IngestRun.id)).scalar()
    print(f"Ingestion Runs: {run_count:,}")

    if run_count > 0:
        # Latest run
        latest_run = (
            db.query(IngestRun)
            .order_by(IngestRun.start_time.desc())
            .first()
        )

        if latest_run:
            print(f"\n  Latest run:")
            print(f"    Query: {latest_run.query}")
            print(f"    Time: {latest_run.start_time}")
            print(f"    Status: {latest_run.status}")
            print(f"    Success: {latest_run.success_count}/{latest_run.total_count}")

    print()

    # Milvus info
    try:
        from vector.milvus_store import MilvusStore

        store = MilvusStore()
        vector_count = store.count()
        print(f"Vector Embeddings: {vector_count:,}")
    except Exception as e:
        print(f"Vector Embeddings: Error - {e}")

    print()
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
