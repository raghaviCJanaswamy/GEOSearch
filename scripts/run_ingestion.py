#!/usr/bin/env python3
"""
Script to run GEO data ingestion from NCBI.
"""
import argparse
import logging
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from geo_ingest.ingest_pipeline import IngestionPipeline
from db import get_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Ingest GEO datasets from NCBI")
    parser.add_argument(
        "--query",
        type=str,
        required=True,
        help="NCBI search query (e.g., 'breast cancer RNA-seq')",
    )
    parser.add_argument(
        "--retmax",
        type=int,
        default=100,
        help="Maximum number of results to fetch (default: 100)",
    )
    parser.add_argument(
        "--mindate",
        type=str,
        help="Minimum date filter (YYYY/MM/DD)",
    )
    parser.add_argument(
        "--maxdate",
        type=str,
        help="Maximum date filter (YYYY/MM/DD)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Batch size for processing (default: 10)",
    )

    args = parser.parse_args()

    logger.info("Starting GEO ingestion")
    logger.info(f"Query: {args.query}")
    logger.info(f"Max results: {args.retmax}")

    # Get database session
    db = next(get_db())

    try:
        pipeline = IngestionPipeline(db)

        stats = pipeline.ingest_by_query(
            query=args.query,
            retmax=args.retmax,
            mindate=args.mindate,
            maxdate=args.maxdate,
            skip_existing=True,
        )

        logger.info("Ingestion completed successfully")
        total = stats['success'] + stats['errors'] + stats['skipped']
        logger.info(f"Total: {total}, Success: {stats['success']}, Errors: {stats['errors']}, Skipped: {stats['skipped']}")

    except Exception as e:
        logger.error(f"Ingestion failed: {e}", exc_info=True)
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
