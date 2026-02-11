#!/usr/bin/env python
"""
Database verification and status script.
Check database health, schema, and data statistics.
Safe to run at any time.
"""
import logging
import sys
from datetime import datetime
from textwrap import indent

from sqlalchemy import inspect, text

from config import settings
from db import get_db, SessionLocal, engine
from db.models import GSESeries, MeshTerm, IngestRun, GSEMesh, IngestItem

logging.basicConfig(
    level=logging.INFO,
    format='%(message)s'
)
logger = logging.getLogger(__name__)


def print_section(title):
    """Print a formatted section header."""
    logger.info("")
    logger.info("=" * 70)
    logger.info(f"  {title}")
    logger.info("=" * 70)


def check_connection():
    """Check database connection and basic connectivity."""
    print_section("Database Connection")
    try:
        db = next(get_db())
        result = db.execute(text("SELECT 1")).fetchone()
        db.close()
        
        if result:
            logger.info("✓ Database connection: OK")
            logger.info(f"  Host: {settings.postgres_host}:{settings.postgres_port}")
            logger.info(f"  Database: {settings.postgres_db}")
            logger.info(f"  User: {settings.postgres_user}")
            return True
    except Exception as e:
        logger.error(f"✗ Database connection: FAILED")
        logger.error(f"  Error: {e}")
        return False


def check_schema():
    """Check database schema and tables."""
    print_section("Database Schema")
    try:
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        
        expected_tables = [
            'gse_series',
            'mesh_term',
            'ingest_run',
            'ingest_item',
            'gse_mesh',
        ]
        
        logger.info(f"Found {len(tables)} table(s):")
        
        for table in sorted(tables):
            status = "✓" if table in expected_tables else "?"
            logger.info(f"  {status} {table}")
        
        missing = [t for t in expected_tables if t not in tables]
        if missing:
            logger.warning(f"\n⚠ Missing tables: {', '.join(missing)}")
            return False
        
        logger.info(f"\n✓ All expected tables present ({len(expected_tables)})")
        return True
    except Exception as e:
        logger.error(f"✗ Schema check failed: {e}")
        return False


def check_data():
    """Check data statistics."""
    print_section("Data Statistics")
    try:
        db = next(get_db())
        
        gse_count = db.query(GSESeries).count()
        mesh_count = db.query(MeshTerm).count()
        ingest_runs = db.query(IngestRun).count()
        ingest_items = db.query(IngestItem).count()
        gse_mesh = db.query(GSEMesh).count()
        
        db.close()
        
        logger.info("Record counts:")
        logger.info(f"  • GSE Series: {gse_count:,}")
        logger.info(f"  • MeSH Terms: {mesh_count:,}")
        logger.info(f"  • Ingestion Runs: {ingest_runs:,}")
        logger.info(f"  • Ingestion Items: {ingest_items:,}")
        logger.info(f"  • GSE-MeSH Associations: {gse_mesh:,}")
        
        if gse_count == 0:
            logger.info("\nℹ No GEO data ingested yet.")
            logger.info("  To add data:")
            logger.info("    1. Open http://localhost:8501")
            logger.info("    2. Click '📥 Data Ingestion' in sidebar")
            logger.info("    3. Enter search query (e.g., 'cancer')")
            logger.info("    4. Click 'Start Ingestion'")
        
        return True
    except Exception as e:
        logger.error(f"✗ Data check failed: {e}")
        return False


def check_gse_details():
    """Show details of GSE records if any exist."""
    print_section("GSE Records Summary")
    try:
        db = next(get_db())
        gse_count = db.query(GSESeries).count()
        
        if gse_count == 0:
            logger.info("No GSE records in database yet.")
            db.close()
            return True
        
        # Get date range
        from sqlalchemy import func
        min_date, max_date = db.query(
            func.min(GSESeries.submission_date),
            func.max(GSESeries.submission_date)
        ).first()
        
        logger.info(f"Total records: {gse_count:,}")
        
        if min_date and max_date:
            logger.info(f"Date range: {min_date.date()} to {max_date.date()}")
        
        # Get top organisms
        organisms_query = db.query(
            func.jsonb_array_elements_text(GSESeries.organisms).label("org"),
            func.count().label("count")
        ).group_by("org").order_by(func.count().desc()).limit(5)
        
        organisms = organisms_query.all()
        if organisms:
            logger.info("\nTop organisms:")
            for org, count in organisms:
                logger.info(f"  • {org}: {count}")
        
        # Get tech types
        tech_query = db.query(
            GSESeries.tech_type,
            func.count()
        ).filter(GSESeries.tech_type.isnot(None)).group_by(
            GSESeries.tech_type
        ).order_by(func.count().desc()).limit(5)
        
        techs = tech_query.all()
        if techs:
            logger.info("\nTop technology types:")
            for tech, count in techs:
                logger.info(f"  • {tech}: {count}")
        
        db.close()
        return True
    except Exception as e:
        logger.error(f"✗ GSE details check failed: {e}")
        return False


def check_ingestion_history():
    """Show recent ingestion runs if any exist."""
    print_section("Recent Ingestion Runs")
    try:
        db = next(get_db())
        runs = db.query(IngestRun).order_by(
            IngestRun.start_time.desc()
        ).limit(5).all()
        
        if not runs:
            logger.info("No ingestion runs yet.")
            db.close()
            return True
        
        logger.info(f"Recent runs (showing {len(runs)}):")
        logger.info("")
        
        for run in runs:
            status_icon = {
                "completed": "✓",
                "running": "⏳",
                "failed": "✗",
                "partial": "⚠",
            }.get(run.status, "?")
            
            duration = ""
            if run.end_time and run.start_time:
                secs = (run.end_time - run.start_time).total_seconds()
                duration = f" ({secs:.0f}s)"
            
            logger.info(f"  {status_icon} Run #{run.id}: {run.query[:40]}{duration}")
            logger.info(f"     Status: {run.status}")
            logger.info(f"     Results: {run.total_count or 0} total, " +
                       f"{run.success_count or 0} success, " +
                       f"{run.error_count or 0} errors")
            logger.info("")
        
        db.close()
        return True
    except Exception as e:
        logger.error(f"✗ Ingestion history check failed: {e}")
        return False


def generate_report():
    """Generate complete health report."""
    print_section("GEOSearch Database Health Report")
    
    logger.info(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("")
    
    # Run all checks
    checks = [
        ("Connection", check_connection),
        ("Schema", check_schema),
        ("Data", check_data),
        ("GSE Details", check_gse_details),
        ("Ingestion History", check_ingestion_history),
    ]
    
    results = []
    for name, check_func in checks:
        try:
            result = check_func()
            results.append((name, result))
        except Exception as e:
            logger.error(f"✗ {name} check crashed: {e}")
            results.append((name, False))
    
    # Summary
    print_section("Health Summary")
    
    all_passed = all(result for _, result in results)
    
    for name, result in results:
        status = "✓ OK" if result else "✗ FAILED"
        logger.info(f"  {status}: {name}")
    
    logger.info("")
    
    if all_passed:
        logger.info("✓ Database is healthy and ready to use")
        logger.info("")
        logger.info("Next steps:")
        logger.info("  1. Open http://localhost:8501")
        logger.info("  2. Start using GEOSearch application")
    else:
        logger.error("✗ Database has issues that need attention")
        logger.error("")
        logger.error("Troubleshooting:")
        logger.error("  • Check PostgreSQL is running")
        logger.error("  • Verify database credentials")
        logger.error("  • Review logs: docker compose -f docker-compose.prod.yml logs postgres")
    
    logger.info("")
    
    return all_passed


def main():
    """Main entry point."""
    try:
        success = generate_report()
        return 0 if success else 1
    except KeyboardInterrupt:
        logger.info("\n\nInterrupted by user")
        return 1
    except Exception as e:
        logger.error(f"\nFatal error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
