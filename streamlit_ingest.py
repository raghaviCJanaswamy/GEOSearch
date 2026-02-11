"""
Streamlit UI components for data ingestion.
Provides interface to run GEO data ingestion from the Streamlit app.
"""
import logging
from datetime import datetime
from typing import Any

import streamlit as st
from sqlalchemy.orm import Session

from db import IngestRun, get_db
from geo_ingest.ingest_pipeline import IngestionPipeline

logger = logging.getLogger(__name__)


def show_ingestion_interface() -> None:
    """Display data ingestion interface in Streamlit."""
    st.header("📥 Data Ingestion")
    st.write(
        "Ingest GEO datasets directly from NCBI into your local database."
    )

    # Check database connectivity
    try:
        db = next(get_db())
        db_available = True
        db.close()
    except Exception as e:
        db_available = False
        st.warning(
            f"⚠️ **Database Connection Issue**: {str(e)}\n\n"
            "This is normal on first launch. The system is initializing.\n\n"
            "**What's happening:**\n"
            "- PostgreSQL is starting up\n"
            "- Tables are being created\n"
            "- Please wait 30-60 seconds and refresh the page\n\n"
            "**In the meantime, you can:**\n"
            "- Review the Configuration tab to see current settings\n"
            "- Check that NCBI_EMAIL is set in your .env file"
        )

    # Create tabs for different ingestion methods
    tab1, tab2, tab3 = st.tabs(
        ["🔍 Query Search", "📋 Ingestion History", "⚙️ Configuration"]
    )

    with tab1:
        show_query_ingestion()

    with tab2:
        if db_available:
            show_ingestion_history()
        else:
            st.info("Ingestion history will be available once database is ready.")

    with tab3:
        show_ingestion_config()


def show_query_ingestion() -> None:
    """Show interface for ingesting by search query."""
    st.subheader("Search and Ingest")

    # Query input
    query = st.text_input(
        "Search Query",
        placeholder="e.g., 'breast cancer RNA-seq' or 'melanoma microarray'",
        help="Enter your NCBI search query",
    )

    # Advanced options
    col1, col2 = st.columns(2)

    with col1:
        retmax = st.number_input(
            "Number of Results",
            min_value=1,
            max_value=10000,
            value=50,
            step=10,
            help="Maximum number of GEO records to fetch",
        )

    with col2:
        skip_existing = st.checkbox(
            "Skip Existing Records",
            value=True,
            help="Don't re-ingest datasets already in database",
        )

    # Date range filter (optional)
    st.markdown("**Date Range (Optional)**")
    date_col1, date_col2 = st.columns(2)

    with date_col1:
        mindate = st.date_input(
            "From Date",
            value=None,
            help="Leave empty to ignore",
        )

    with date_col2:
        maxdate = st.date_input(
            "To Date",
            value=None,
            help="Leave empty to ignore",
        )

    # Format dates for NCBI API
    mindate_str = mindate.strftime("%Y/%m/%d") if mindate else None
    maxdate_str = maxdate.strftime("%Y/%m/%d") if maxdate else None

    # Start ingestion button
    if st.button("🚀 Start Ingestion", type="primary", use_container_width=True):
        if not query:
            st.error("Please enter a search query")
            return

        # Check database before starting
        try:
            db_test = next(get_db())
            db_test.close()
        except Exception as e:
            st.error(
                f"Cannot start ingestion: Database not ready\n\n"
                f"Error: {str(e)}\n\n"
                f"**Please wait**: The system is initializing PostgreSQL.\n"
                f"Refresh the page in 30-60 seconds."
            )
            return

        ingest_with_progress(
            query=query,
            retmax=retmax,
            mindate=mindate_str,
            maxdate=maxdate_str,
            skip_existing=skip_existing,
        )


def ingest_with_progress(
    query: str,
    retmax: int,
    mindate: str | None = None,
    maxdate: str | None = None,
    skip_existing: bool = True,
) -> None:
    """Run ingestion with progress display."""
    # Create progress containers
    progress_container = st.container()
    status_container = st.container()

    try:
        with progress_container:
            progress_bar = st.progress(0)
            status_text = st.empty()

            # Get database session
            try:
                db = next(get_db())
            except Exception as db_err:
                st.error(
                    f"❌ Database Connection Failed\n\n"
                    f"Error: {str(db_err)}\n\n"
                    f"**The system is still initializing.** Please:\n"
                    f"1. Wait 30-60 seconds\n"
                    f"2. Refresh the page (press F5)\n"
                    f"3. Try again"
                )
                return

            # Create ingestion pipeline
            try:
                pipeline = IngestionPipeline(db)
            except Exception as pipeline_err:
                st.error(
                    f"❌ Failed to initialize ingestion pipeline\n\n"
                    f"Error: {str(pipeline_err)}\n\n"
                    f"**Possible causes:**\n"
                    f"- Database tables not yet created\n"
                    f"- Database schema mismatch\n\n"
                    f"**Solution**: Refresh the page and wait a moment."
                )
                return

            # Create ingestion run record
            try:
                run = IngestRun(
                    query=query,
                    start_time=datetime.utcnow(),
                    status="running",
                    run_metadata={
                        "retmax": retmax,
                        "mindate": mindate,
                        "maxdate": maxdate,
                        "skip_existing": skip_existing,
                    },
                )
                db.add(run)
                db.commit()
                run_id = run.id
            except Exception as run_err:
                st.error(
                    f"❌ Failed to create ingestion run\n\n"
                    f"Error: {str(run_err)}\n\n"
                    f"Database may not be fully initialized yet."
                )
                return

            # Update status
            status_text.info(f"⏳ Initializing ingestion (Run ID: {run_id})...")

            # Run ingestion
            stats = pipeline.ingest_by_query(
                query=query,
                retmax=retmax,
                mindate=mindate,
                maxdate=maxdate,
                skip_existing=skip_existing,
            )

            # Update progress
            progress_bar.progress(100)

            # Display results
            with status_container:
                st.success("✅ Ingestion Completed!")

                col1, col2, col3, col4 = st.columns(4)

                with col1:
                    st.metric("Total Records", stats.get("total", 0))

                with col2:
                    st.metric("Successfully Ingested", stats.get("success", 0))

                with col3:
                    st.metric("Errors", stats.get("errors", 0))

                with col4:
                    st.metric("Skipped", stats.get("skipped", 0))

                # Display details
                if stats.get("errors") > 0 and stats.get("error_details"):
                    with st.expander("View Error Details"):
                        for error in stats["error_details"][:10]:
                            st.warning(f"- {error}")

    except Exception as e:
        logger.error(f"Ingestion failed: {str(e)}")
        with status_container:
            st.error(f"❌ Ingestion Failed: {str(e)}")


def show_ingestion_history() -> None:
    """Display ingestion history and statistics."""
    st.subheader("Ingestion History")

    try:
        db = next(get_db())
    except Exception as e:
        st.warning(f"Cannot access ingestion history: Database not ready\n\nError: {str(e)}")
        return

    try:
        # Get recent ingestion runs
        runs = db.query(IngestRun).order_by(IngestRun.start_time.desc()).limit(20).all()

        if not runs:
            st.info("No ingestion runs yet. Start by searching and ingesting data!")
            return

        # Create display dataframe
        history_data = []
        for run in runs:
            history_data.append({
                "ID": run.id,
                "Query": run.query,
                "Status": run.status,
                "Total": run.total_count or 0,
                "Success": run.success_count or 0,
                "Errors": run.error_count or 0,
                "Started": run.start_time.strftime("%Y-%m-%d %H:%M:%S") if run.start_time else "-",
                "Duration": (
                    str(run.end_time - run.start_time).split(".")[0]
                    if run.end_time and run.start_time
                    else "-"
                ),
            })

        # Display as table
        st.dataframe(history_data, use_container_width=True)

        # Show statistics
        st.subheader("Ingestion Statistics")

        col1, col2, col3, col4 = st.columns(4)

        total_runs = len(runs)
        total_records = sum(r.total_count or 0 for r in runs)
        total_success = sum(r.success_count or 0 for r in runs)
        total_errors = sum(r.error_count or 0 for r in runs)

        with col1:
            st.metric("Total Runs", total_runs)

        with col2:
            st.metric("Total Records Fetched", total_records)

        with col3:
            st.metric("Total Successful", total_success)

        with col4:
            st.metric("Total Errors", total_errors)

    except Exception as e:
        st.error(f"Error loading ingestion history: {str(e)}")


def show_ingestion_config() -> None:
    """Display ingestion configuration options."""
    st.subheader("Configuration")

    from config import settings

    # Display current settings
    st.markdown("**Current NCBI Settings:**")

    col1, col2 = st.columns(2)

    with col1:
        st.info(f"📧 NCBI Email: {settings.ncbi_email}")

    with col2:
        has_api_key = "✅ Set" if settings.ncbi_api_key else "❌ Not Set"
        st.info(f"🔑 NCBI API Key: {has_api_key}")

    # Rate limiting info
    st.markdown("**Rate Limiting:**")
    st.info(
        f"⏱️ Rate Limit: {settings.rate_limit_qps} queries per second\n\n"
        "This prevents overwhelming NCBI servers and respects their usage policies."
    )

    # Recommendations
    st.markdown("**Recommendations for Better Ingestion:**")
    st.markdown(
        """
    1. **Set NCBI Email** (Required)
       - Configure in `.env` file: `NCBI_EMAIL=your.email@example.com`
       - NCBI requires this to track usage

    2. **Add NCBI API Key** (Optional but Recommended)
       - Get free API key at: https://www.ncbi.nlm.nih.gov/account/
       - Increases rate limit from 3 to 10 queries/second
       - Configure in `.env`: `NCBI_API_KEY=your-api-key`

    3. **Start with Smaller Batches**
       - Test with 50-100 records first
       - Gradually increase after confirming it works
       - Large batches (10000+) may take considerable time

    4. **Use Specific Queries**
       - More specific queries return better results
       - Example: "breast cancer RNA-seq GPL570"
       - vs. just "cancer" (too broad)
    """
    )

    # Database statistics
    st.markdown("**Database Statistics:**")

    try:
        db = next(get_db())
        series_count = db.query(IngestRun.total_count).first()[0] or 0
        total_ingested = db.query(IngestRun.success_count).first()[0] or 0

        col1, col2 = st.columns(2)
        with col1:
            st.metric("Records in Database", total_ingested)
        with col2:
            st.metric("Total Processed", series_count)

    except Exception as e:
        st.warning(f"Could not fetch database stats: {str(e)}")


def show_quick_ingest_button() -> None:
    """Show quick ingest button in sidebar."""
    with st.sidebar:
        st.markdown("---")
        if st.button("📥 Data Ingestion", use_container_width=True):
            st.session_state.show_ingest = True
