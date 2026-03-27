"""
Main ingestion pipeline for GEO metadata.
CLI tool for fetching and storing GEO Series data.
"""
import argparse
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session
from tqdm import tqdm

from config import settings
from db import IngestItem, IngestRun, get_db, init_db
from db.models import GSESeries

from geo_ingest.ncbi_client import NCBIClient
from geo_ingest.parser import GEOParser
from mesh.matcher import MeSHMatcher
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
        progress_callback: Any = None,
    ) -> dict[str, Any]:
        """
        Ingest GEO datasets by search query.

        Args:
            query: NCBI search query
            retmax: Maximum number of results to fetch
            mindate: Minimum date filter (YYYY/MM/DD)
            maxdate: Maximum date filter (YYYY/MM/DD)
            skip_existing: Skip datasets already in database
            progress_callback: Optional callback function(stage, current, total, message)

        Returns:
            Ingestion statistics dictionary
        """
        logger.info(f"Starting ingestion: query='{query}', retmax={retmax}")

        # Create ingestion run record
        run = IngestRun(
            query=query,
            start_time=datetime.now(timezone.utc),
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
            if progress_callback:
                progress_callback("search", 0, 1, "Searching NCBI for records...")
            
            gse_ids = self.ncbi_client.search_gse(
                query=query,
                retmax=retmax,
                mindate=mindate,
                maxdate=maxdate,
            )

            if not gse_ids:
                logger.warning("No GSE records found")
                run.status = "completed"
                run.end_time = datetime.now(timezone.utc)
                run.total_count = 0
                self.db.commit()
                return {"total": 0, "success": 0, "errors": 0, "skipped": 0}

            # Get GSE accessions from IDs — single batch call, reuse data downstream
            summaries = self.ncbi_client.fetch_gse_summary(gse_ids)
            # Build accession→summary map to avoid re-fetching per record
            accession_summaries: dict[str, dict] = {}
            for uid, summary in summaries.items():
                acc = summary.get("accession", "")
                if acc and acc.startswith("GSE"):
                    accession_summaries[acc] = summary
            accessions = list(accession_summaries.keys())

            logger.info(f"Found {len(accessions)} GSE accessions")

            # Filter existing if needed
            skipped_count = 0
            if skip_existing:
                existing = (
                    self.db.query(GSESeries.accession)
                    .filter(GSESeries.accession.in_(accessions))
                    .all()
                )
                existing_set = {row[0] for row in existing}
                skipped_count = len(existing_set)
                accessions = [acc for acc in accessions if acc not in existing_set]
                accession_summaries = {k: v for k, v in accession_summaries.items() if k in accessions}
                logger.info(f"Skipped {skipped_count} existing records, processing {len(accessions)}")

            run.total_count = len(accessions) + skipped_count
            self.db.commit()

            # Process each accession using pre-fetched summary data
            results = self._process_accessions(run.id, accessions, progress_callback, accession_summaries)
            results["skipped"] = skipped_count
            results["total"] = len(accessions) + skipped_count

            # Update run status
            run.end_time = datetime.now(timezone.utc)
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
            run.end_time = datetime.now(timezone.utc)
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
            start_time=datetime.now(timezone.utc),
            status="running",
            run_metadata={"accessions": accessions, "mode": "manual"},
        )
        self.db.add(run)
        self.db.commit()

        try:
            # Filter existing
            skipped_count = 0
            if skip_existing:
                existing = (
                    self.db.query(GSESeries.accession)
                    .filter(GSESeries.accession.in_(accessions))
                    .all()
                )
                existing_set = {row[0] for row in existing}
                skipped_count = len(existing_set)
                accessions = [acc for acc in accessions if acc not in existing_set]
                logger.info(f"Skipped {skipped_count} existing, processing {len(accessions)}")

            run.total_count = len(accessions) + skipped_count
            self.db.commit()

            results = self._process_accessions(run.id, accessions)
            results["skipped"] = skipped_count

            run.end_time = datetime.now(timezone.utc)
            run.success_count = results["success"]
            run.error_count = results["errors"]
            run.status = "completed" if results["errors"] == 0 else "partial"
            self.db.commit()

            return results

        except Exception as e:
            logger.error(f"Ingestion failed: {e}", exc_info=True)
            run.status = "failed"
            run.end_time = datetime.now(timezone.utc)
            self.db.commit()
            raise

    def _process_accessions(
        self,
        run_id: int,
        accessions: list[str],
        progress_callback: Any = None,
        prefetched_summaries: dict[str, dict] | None = None,
    ) -> dict[str, int]:
        """
        Process list of accessions: fetch (or use pre-fetched), parse, store.
        Embeddings are generated in one batched call at the end for speed.

        Args:
            run_id: Ingestion run ID
            accessions: List of GSE accessions
            progress_callback: Optional callback function(stage, current, total, message)
            prefetched_summaries: Optional dict of accession→raw summary (avoids re-fetching)

        Returns:
            Statistics dictionary
        """
        stats = {"success": 0, "errors": 0, "skipped": 0}
        total = len(accessions)

        # Collect parsed records for batch embedding
        parsed_records: list[tuple[str, dict]] = []  # (accession, parsed)

        for idx, accession in enumerate(tqdm(accessions, desc="Processing GSE records"), 1):
            item = IngestItem(run_id=run_id, accession=accession, status="pending")
            self.db.add(item)

            try:
                if progress_callback:
                    progress_callback("process", idx, total, f"Fetching {accession}...")

                # Use pre-fetched data if available, otherwise make an API call
                if prefetched_summaries and accession in prefetched_summaries:
                    raw_data = self.ncbi_client._build_parsed_from_summary(
                        accession, prefetched_summaries[accession]
                    )
                else:
                    raw_data = self.ncbi_client.fetch_gse_text(accession)

                item.fetch_time = datetime.now(timezone.utc)

                if "error" in raw_data:
                    item.status = "failed"
                    item.error_message = raw_data["error"]
                    self.db.commit()
                    stats["errors"] += 1
                    if progress_callback:
                        progress_callback("process", idx, total, f"❌ Error: {accession}")
                    continue

                if progress_callback:
                    progress_callback("process", idx, total, f"Parsing {accession}...")

                parsed = self.parser.parse_gse_metadata(raw_data)
                if not parsed:
                    item.status = "failed"
                    item.error_message = "Failed to parse metadata"
                    self.db.commit()
                    stats["errors"] += 1
                    continue

                if progress_callback:
                    progress_callback("process", idx, total, f"Storing {accession}...")

                gse = GSESeries(**parsed)
                self.db.merge(gse)
                item.status = "completed"
                item.process_time = datetime.now(timezone.utc)
                # Single commit per record (not 7)
                self.db.commit()

                parsed_records.append((accession, parsed))
                stats["success"] += 1

                if progress_callback:
                    progress_callback("process", idx, total, f"✅ Completed: {accession}")

            except Exception as e:
                logger.error(f"Failed to process {accession}: {e}", exc_info=True)
                item.status = "failed"
                item.error_message = str(e)
                self.db.commit()
                stats["errors"] += 1
                if progress_callback:
                    progress_callback("process", idx, total, f"❌ Error: {accession}")

        # Batch embed all successful records in one call
        if parsed_records:
            if progress_callback:
                progress_callback("process", total, total, f"Generating embeddings for {len(parsed_records)} records...")
            try:
                texts = [self.parser.prepare_embedding_text(p) for _, p in parsed_records]
                embeddings = self.embedding_provider.embed_texts(texts)
                pairs = [(acc, emb) for (acc, _), emb in zip(parsed_records, embeddings)]
                self.vector_store.upsert_embeddings(pairs)
                logger.info(f"Upserted {len(pairs)} embeddings in one batch")
            except Exception as e:
                logger.error(f"Batch embedding failed: {e}", exc_info=True)

            # Auto-tag new records with MeSH terms
            if progress_callback:
                progress_callback("process", total, total, f"Tagging {len(parsed_records)} records with MeSH terms...")
            try:
                new_accessions = [acc for acc, _ in parsed_records]
                matcher = MeSHMatcher(self.db)
                n_associations = matcher.tag_gse_batch(new_accessions, confidence_threshold=0.3)
                logger.info(f"Auto-tagged {len(new_accessions)} records with {n_associations} MeSH associations")
            except Exception as e:
                # Non-fatal: MeSH terms may not be loaded yet
                logger.warning(f"MeSH auto-tagging skipped: {e}")

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
