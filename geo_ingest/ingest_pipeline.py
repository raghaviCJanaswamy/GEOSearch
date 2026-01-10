"""
Main ingestion pipeline for GEO metadata.
CLI tool for fetching and storing GEO Series data.
"""
import argparse
import logging
import sys
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session
from tqdm import tqdm

from config import settings
from db import IngestItem, IngestRun, get_db, init_db
from db.models import GSESeries
from geo_ingest.ncbi_client import NCBIClient
from geo_ingest.parser import GEOParser
from vector.embeddings import get_embedding_provider
from vector.milvus_store import MilvusStore

# Configure logging
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class IngestionPipeline:
    """Pipeline for ingesting GEO metadata into database and vector store."""

    def __init__(self, db: Session):
        """
        Initialize ingestion pipeline.

        Args:
            db: Database session
        """
        self.db = db
        self.ncbi_client = NCBIClient()
        self.parser = GEOParser()
        self.embedding_provider = get_embedding_provider()
        self.vector_store = MilvusStore()

    def ingest_by_query(
        self,
        query: str,
        retmax: int = 100,
        mindate: str | None = None,
        maxdate: str | None = None,
        skip_existing: bool = True,
    ) -> dict[str, Any]:
        """
        Ingest GEO datasets by search query.

        Args:
            query: NCBI search query
            retmax: Maximum number of results to fetch
            mindate: Minimum date filter (YYYY/MM/DD)
            maxdate: Maximum date filter (YYYY/MM/DD)
            skip_existing: Skip datasets already in database

        Returns:
            Ingestion statistics dictionary
        """
        logger.info(f"Starting ingestion: query='{query}', retmax={retmax}")

        # Create ingestion run record
        run = IngestRun(
            query=query,
            start_time=datetime.utcnow(),
            status="running",
            run_metadata={
                "retmax": retmax,
                "mindate": mindate,
                "maxdate": maxdate,
            },
        )
        self.db.add(run)
        self.db.commit()

        try:
            # Search for GSE IDs
            gse_ids = self.ncbi_client.search_gse(
                query=query,
                retmax=retmax,
                mindate=mindate,
                maxdate=maxdate,
            )

            if not gse_ids:
                logger.warning("No GSE records found")
                run.status = "completed"
                run.end_time = datetime.utcnow()
                run.total_count = 0
                self.db.commit()
                return {"total": 0, "success": 0, "errors": 0, "skipped": 0}

            # Get GSE accessions from IDs
            summaries = self.ncbi_client.fetch_gse_summary(gse_ids)
            accessions = []
            for uid, summary in summaries.items():
                acc = summary.get("accession", "")
                if acc and acc.startswith("GSE"):
                    accessions.append(acc)

            logger.info(f"Found {len(accessions)} GSE accessions")

            # Filter existing if needed
            if skip_existing:
                existing = (
                    self.db.query(GSESeries.accession)
                    .filter(GSESeries.accession.in_(accessions))
                    .all()
                )
                existing_set = {row[0] for row in existing}
                accessions = [acc for acc in accessions if acc not in existing_set]
                logger.info(f"Skipped {len(existing_set)} existing records, processing {len(accessions)}")

            run.total_count = len(accessions)
            self.db.commit()

            # Process each accession
            results = self._process_accessions(run.id, accessions)

            # Update run status
            run.end_time = datetime.utcnow()
            run.success_count = results["success"]
            run.error_count = results["errors"]
            run.status = "completed" if results["errors"] == 0 else "partial"
            self.db.commit()

            logger.info(
                f"Ingestion completed: {results['success']} success, "
                f"{results['errors']} errors, {results['skipped']} skipped"
            )

            return results

        except Exception as e:
            logger.error(f"Ingestion failed: {e}", exc_info=True)
            run.status = "failed"
            run.end_time = datetime.utcnow()
            self.db.commit()
            raise

    def ingest_by_accessions(
        self,
        accessions: list[str],
        skip_existing: bool = True,
    ) -> dict[str, Any]:
        """
        Ingest specific GSE accessions.

        Args:
            accessions: List of GSE accessions
            skip_existing: Skip datasets already in database

        Returns:
            Ingestion statistics
        """
        logger.info(f"Starting ingestion by accessions: {len(accessions)} total")

        # Create run record
        run = IngestRun(
            query=f"Manual accession list: {', '.join(accessions[:5])}{'...' if len(accessions) > 5 else ''}",
            start_time=datetime.utcnow(),
            status="running",
            run_metadata={"accessions": accessions, "mode": "manual"},
        )
        self.db.add(run)
        self.db.commit()

        try:
            # Filter existing
            if skip_existing:
                existing = (
                    self.db.query(GSESeries.accession)
                    .filter(GSESeries.accession.in_(accessions))
                    .all()
                )
                existing_set = {row[0] for row in existing}
                accessions = [acc for acc in accessions if acc not in existing_set]
                logger.info(f"Skipped {len(existing_set)} existing, processing {len(accessions)}")

            run.total_count = len(accessions)
            self.db.commit()

            results = self._process_accessions(run.id, accessions)

            run.end_time = datetime.utcnow()
            run.success_count = results["success"]
            run.error_count = results["errors"]
            run.status = "completed" if results["errors"] == 0 else "partial"
            self.db.commit()

            return results

        except Exception as e:
            logger.error(f"Ingestion failed: {e}", exc_info=True)
            run.status = "failed"
            run.end_time = datetime.utcnow()
            self.db.commit()
            raise

    def _process_accessions(self, run_id: int, accessions: list[str]) -> dict[str, int]:
        """
        Process list of accessions: fetch, parse, store.

        Args:
            run_id: Ingestion run ID
            accessions: List of GSE accessions

        Returns:
            Statistics dictionary
        """
        stats = {"success": 0, "errors": 0, "skipped": 0}

        for accession in tqdm(accessions, desc="Processing GSE records"):
            item = IngestItem(run_id=run_id, accession=accession, status="pending")
            self.db.add(item)
            self.db.commit()

            try:
                # Fetch
                item.status = "fetching"
                self.db.commit()

                raw_data = self.ncbi_client.fetch_gse_text(accession)
                item.fetch_time = datetime.utcnow()

                if "error" in raw_data:
                    item.status = "failed"
                    item.error_message = raw_data["error"]
                    self.db.commit()
                    stats["errors"] += 1
                    continue

                # Parse
                item.status = "parsing"
                self.db.commit()

                parsed = self.parser.parse_gse_metadata(raw_data)
                if not parsed:
                    item.status = "failed"
                    item.error_message = "Failed to parse metadata"
                    self.db.commit()
                    stats["errors"] += 1
                    continue

                # Store in database
                item.status = "storing"
                self.db.commit()

                gse = GSESeries(**parsed)
                self.db.merge(gse)  # Upsert
                self.db.commit()

                # Generate and store embedding
                embedding_text = self.parser.prepare_embedding_text(parsed)
                embedding = self.embedding_provider.embed_texts([embedding_text])[0]
                self.vector_store.upsert_embeddings([(accession, embedding)])

                # Success
                item.status = "completed"
                item.process_time = datetime.utcnow()
                self.db.commit()
                stats["success"] += 1

            except Exception as e:
                logger.error(f"Failed to process {accession}: {e}", exc_info=True)
                item.status = "failed"
                item.error_message = str(e)
                self.db.commit()
                stats["errors"] += 1

        return stats


def main() -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Ingest GEO Series metadata from NCBI into GEOSearch"
    )

    subparsers = parser.add_subparsers(dest="command", help="Ingestion command")

    # Query-based ingestion
    query_parser = subparsers.add_parser("query", help="Ingest by search query")
    query_parser.add_argument("--query", "-q", required=True, help="NCBI search query")
    query_parser.add_argument(
        "--retmax", "-n", type=int, default=100, help="Maximum results (default: 100)"
    )
    query_parser.add_argument("--mindate", help="Minimum date (YYYY/MM/DD)")
    query_parser.add_argument("--maxdate", help="Maximum date (YYYY/MM/DD)")
    query_parser.add_argument(
        "--force", action="store_true", help="Re-ingest existing records"
    )

    # Accession-based ingestion
    acc_parser = subparsers.add_parser("accessions", help="Ingest by accession list")
    acc_parser.add_argument(
        "accessions", nargs="+", help="GSE accessions (e.g., GSE123456)"
    )
    acc_parser.add_argument(
        "--force", action="store_true", help="Re-ingest existing records"
    )

    # Init database
    init_parser = subparsers.add_parser("init", help="Initialize database")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # Initialize database
    if args.command == "init":
        logger.info("Initializing database...")
        init_db()
        logger.info("Database initialized successfully")
        return 0

    # Get database session
    db_gen = get_db()
    db = next(db_gen)

    try:
        pipeline = IngestionPipeline(db)

        if args.command == "query":
            results = pipeline.ingest_by_query(
                query=args.query,
                retmax=args.retmax,
                mindate=args.mindate,
                maxdate=args.maxdate,
                skip_existing=not args.force,
            )
        elif args.command == "accessions":
            results = pipeline.ingest_by_accessions(
                accessions=args.accessions,
                skip_existing=not args.force,
            )
        else:
            parser.print_help()
            return 1

        print(f"\n{'='*60}")
        print("Ingestion Summary")
        print(f"{'='*60}")
        print(f"Total processed: {results.get('total', results['success'] + results['errors'])}")
        print(f"Successful: {results['success']}")
        print(f"Errors: {results['errors']}")
        print(f"Skipped: {results.get('skipped', 0)}")
        print(f"{'='*60}\n")

        return 0 if results["errors"] == 0 else 1

    except KeyboardInterrupt:
        logger.info("Ingestion interrupted by user")
        return 130
    except Exception as e:
        logger.error(f"Ingestion failed: {e}", exc_info=True)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
