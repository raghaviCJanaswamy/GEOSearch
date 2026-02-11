#!/usr/bin/env python
"""
Database initialization and verification script.
Ensures database is properly initialized with all tables and optional sample data.
"""
import logging
import sys
from datetime import datetime
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import text

from config import settings
from db import init_db, get_db, engine
from db.models import GSESeries, MeshTerm, IngestRun, Base

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def check_database_connection():
    """Test database connection."""
    logger.info("Checking database connection...")
    try:
        # Use engine connection for raw SQL
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        logger.info("✓ Database connection successful")
        return True
    except Exception as e:
        logger.error(f"✗ Database connection failed: {e}")
        return False


def create_tables():
    """Create all database tables."""
    logger.info("Creating database tables...")
    try:
        init_db()
        logger.info("✓ Database tables created successfully")
        return True
    except Exception as e:
        logger.error(f"✗ Failed to create tables: {e}")
        return False


def verify_tables_exist():
    """Verify all required tables exist."""
    logger.info("Verifying database tables...")
    try:
        db = next(get_db())
        
        # Check if tables exist by querying them
        tables_to_check = {
            'gse_series': GSESeries,
            'mesh_term': MeshTerm,
            'ingest_run': IngestRun,
        }
        
        for table_name, model in tables_to_check.items():
            try:
                db.query(model).limit(1).all()
                logger.info(f"  ✓ Table '{table_name}' exists")
            except Exception as e:
                logger.error(f"  ✗ Table '{table_name}' missing: {e}")
                return False
        
        db.close()
        logger.info("✓ All required tables exist")
        return True
    except Exception as e:
        logger.error(f"✗ Table verification failed: {e}")
        return False


def get_database_stats():
    """Get current database statistics."""
    logger.info("Getting database statistics...")
    try:
        db = next(get_db())
        
        stats = {
            'gse_count': db.query(GSESeries).count(),
            'mesh_count': db.query(MeshTerm).count(),
            'ingest_runs': db.query(IngestRun).count(),
        }
        
        db.close()
        
        logger.info(f"  • GSE Records: {stats['gse_count']}")
        logger.info(f"  • MeSH Terms: {stats['mesh_count']}")
        logger.info(f"  • Ingestion Runs: {stats['ingest_runs']}")
        
        return stats
    except Exception as e:
        logger.error(f"✗ Failed to get database stats: {e}")
        return None


def main():
    """Main initialization sequence."""
    logger.info("=" * 60)
    logger.info("GEOSearch Database Initialization")
    logger.info("=" * 60)
    logger.info("")
    
    # Step 1: Check connection
    if not check_database_connection():
        logger.error("\n✗ Cannot proceed: Database not accessible")
        logger.info("\nTroubleshooting:")
        logger.info("  • Check PostgreSQL is running")
        logger.info("  • Verify database credentials in .env")
        logger.info("  • Check network connectivity")
        return False
    
    logger.info("")
    
    # Step 2: Create tables
    if not create_tables():
        logger.error("\n✗ Failed to create database tables")
        logger.info("\nTroubleshooting:")
        logger.info("  • Check database permissions")
        logger.info("  • Verify PostgreSQL compatibility")
        return False
    
    logger.info("")
    
    # Step 3: Verify tables
    if not verify_tables_exist():
        logger.error("\n✗ Table verification failed")
        return False
    
    logger.info("")
    
    # Step 4: Get statistics
    stats = get_database_stats()
    if stats is None:
        logger.error("\n✗ Failed to query database statistics")
        return False
    
    logger.info("")
    logger.info("=" * 60)
    logger.info("Database Initialization Complete")
    logger.info("=" * 60)
    logger.info("")
    
    if stats['gse_count'] == 0:
        logger.info("ℹ Database ready for data ingestion")
        logger.info("  Visit http://localhost:8501 to start ingesting data")
    else:
        logger.info(f"✓ Database contains {stats['gse_count']} GSE records")
    
    logger.info("")
    return True


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
