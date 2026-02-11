"""
Streamlit UI components for data ingestion.
Provides interface to run GEO data ingestion from the Streamlit app.
"""
import logging
from datetime import datetime
from typing import Any

import streamlit as st
from sqlalchemy import text
from sqlalchemy.orm import Session

from config import settings
from db import IngestRun, get_db, engine, init_db
from db.models import GSESeries, MeshTerm, IngestRun as IngestRunModel
from geo_ingest.ingest_pipeline import IngestionPipeline
from streamlit_ingest_mesh import show_mesh_loader

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
    tab1, tab2, tab3, tab4 = st.tabs(
        ["🔍 Query Search", "📋 Ingestion History", "⚙️ Configuration", "🗄️ Database"]
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
    
    with tab4:
        show_database_initialization()


def show_query_ingestion() -> None:
    """Show interface for ingesting by search query."""
    st.subheader("Search and Ingest")

    # Quick ingest presets
    st.markdown("### Quick Start")
    quick_presets = {
        "🔬 Cancer Research": {"query": "cancer", "retmax": 50},
        "🧬 RNA-seq": {"query": "RNA-seq", "retmax": 50},
        "🦠 COVID-19": {"query": "COVID-19", "retmax": 50},
        "🧠 Brain": {"query": "brain", "retmax": 50},
    }

    col1, col2, col3, col4 = st.columns(4)
    quick_cols = [col1, col2, col3, col4]

    for idx, (label, params) in enumerate(quick_presets.items()):
        with quick_cols[idx]:
            if st.button(label, use_container_width=True, key=f"quick_{idx}"):
                ingest_with_progress(
                    query=params["query"],
                    retmax=params["retmax"],
                    skip_existing=True,
                )
                st.rerun()

    st.markdown("---")

    # Custom query input
    st.markdown("### Custom Query")
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
    # Create separate containers for different sections
    header_container = st.container()
    progress_container = st.container()
    details_container = st.container()
    results_container = st.container()

    try:
        with header_container:
            st.markdown("### 📥 Ingestion In Progress")
            st.write(f"Query: **{query}** | Max Results: **{retmax}**")

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

        # Progress display
        with progress_container:
            col1, col2 = st.columns([3, 1])
            
            with col1:
                progress_bar = st.progress(0)
                status_text = st.empty()
            
            with col2:
                timer_placeholder = st.empty()
            
            # Metrics row
            metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)
            found_placeholder = metric_col1.empty()
            processed_placeholder = metric_col2.empty()
            success_placeholder = metric_col3.empty()
            error_placeholder = metric_col4.empty()

        # Details section
        with details_container:
            details_expander = st.expander("📋 Ingestion Details (Click to expand)")
            details_log = details_expander.empty()

        # Initialize tracking
        import time
        start_time = time.time()
        log_messages = []

        # Monkey-patch logger to capture messages
        original_handlers = logger.handlers.copy()
        
        class StreamlitLogHandler(logging.Handler):
            def emit(self, record):
                msg = self.format(record)
                if "Processing" in msg or "Fetching" in msg or "Error" in msg or "Skipping" in msg:
                    log_messages.append(msg)
                    # Keep only last 20 messages
                    if len(log_messages) > 20:
                        log_messages.pop(0)
                    details_log.text_area("", "\n".join(log_messages), height=150, disabled=True)

        log_handler = StreamlitLogHandler()
        log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(log_handler)

        try:
            # Update status
            status_text.info(f"🔍 Searching NCBI for records... (Run ID: {run_id})")
            
            # Define progress callback
            def update_progress(stage: str, current: int, total: int, message: str):
                """Callback to update progress in real-time."""
                if stage == "search":
                    status_text.info(f"🔍 {message}")
                elif stage == "process":
                    if total > 0:
                        pct = min(100, int((current / total) * 100))
                        progress_bar.progress(pct)
                        
                        # Update metrics
                        found_placeholder.metric("🔎 Found", total)
                        processed_placeholder.metric("⚙️ Processed", current)
                        
                        # Update timer
                        elapsed = int(time.time() - start_time)
                        mins, secs = divmod(elapsed, 60)
                        timer_placeholder.metric("⏱️ Time", f"{mins}m {secs}s")
                    
                    status_text.info(f"⏳ {message} ({current}/{total})")
                    
                    # Update log
                    log_messages.append(message)
                    if len(log_messages) > 30:
                        log_messages.pop(0)
                    details_log.text_area("", "\n".join(log_messages), height=150, disabled=True)
            
            # Run ingestion with progress callback
            stats = pipeline.ingest_by_query(
                query=query,
                retmax=retmax,
                mindate=mindate,
                maxdate=maxdate,
                skip_existing=skip_existing,
                progress_callback=update_progress,
            )

            # Calculate progress
            total = stats.get("total", 0)
            success = stats.get("success", 0)
            errors = stats.get("errors", 0)
            skipped = stats.get("skipped", 0)
            
            if total > 0:
                progress_pct = min(100, int((success + skipped) / total * 100))
            else:
                progress_pct = 100

            elapsed = int(time.time() - start_time)
            
            # Update progress bar
            progress_bar.progress(progress_pct)
            
            # Update timer
            mins, secs = divmod(elapsed, 60)
            timer_placeholder.metric("⏱️ Time", f"{mins}m {secs}s")
            
            # Update metrics
            found_placeholder.metric("🔎 Found", total)
            processed_placeholder.metric("⚙️ Processed", success + skipped)
            success_placeholder.metric("✅ Success", success)
            error_placeholder.metric("❌ Errors", errors)

            # Update status
            if success > 0:
                status_text.success(f"✅ Ingestion Completed!")
            elif errors > 0:
                status_text.warning(f"⚠️ Ingestion Completed with errors")
            else:
                status_text.info(f"ℹ️ Ingestion Completed (No new records found)")

            # Display final results
            with results_container:
                st.markdown("---")
                st.markdown("### 📊 Ingestion Results")
                
                # Summary metrics
                result_col1, result_col2, result_col3, result_col4 = st.columns(4)

                with result_col1:
                    st.metric("Total Records", total, delta=None)

                with result_col2:
                    st.metric("Successfully Ingested", success, delta=None)

                with result_col3:
                    st.metric("Skipped (Existing)", skipped, delta=None)

                with result_col4:
                    st.metric("Errors", errors, delta=None)

                # Success rate
                if total > 0:
                    success_rate = (success / total) * 100
                    st.progress(success_rate / 100)
                    st.caption(f"Success Rate: {success_rate:.1f}%")

                # Error details
                if errors > 0 and stats.get("error_details"):
                    with st.expander("🔍 View Error Details"):
                        for error in stats["error_details"][:10]:
                            st.warning(f"- {error}")
                        if len(stats["error_details"]) > 10:
                            st.caption(f"... and {len(stats['error_details']) - 10} more errors")

                # Next steps
                if success > 0:
                    st.success("🎉 Data successfully ingested! You can now search and explore the data.")
                    if st.button("🔍 Go to Search Page", use_container_width=True):
                        st.session_state.page = "search"
                        st.rerun()

        finally:
            # Restore logger
            logger.removeHandler(log_handler)

    except Exception as e:
        logger.error(f"Ingestion failed: {str(e)}")
        with results_container:
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

def show_database_initialization() -> None:
    """Display database initialization interface."""
    st.subheader("Database Management")
    st.write(
        "Initialize database tables, load MeSH terms, and view database statistics."
    )

    # Create tabs for different database operations
    db_tab1, db_tab2, db_tab3 = st.tabs(
        ["📊 Database Init", "🏥 MeSH Terms", "📈 Status"]
    )

    with db_tab1:
        st.markdown("### Database Initialization")
        st.write("Ensure all tables are properly created and ready for data ingestion.")

        if st.button(
            "🗄️ Initialize Database",
            type="primary",
            use_container_width=True,
            key="init_db_btn",
        ):
            show_init_progress()

    with db_tab2:
        st.markdown("### MeSH Terms Management")
        st.write(
            "Load Medical Subject Headings (MeSH) into the database for enhanced search capabilities."
        )
        show_mesh_loader()

    with db_tab3:
        st.markdown("### Database Status")
        show_database_stats()


def show_init_progress() -> None:
    """Show database initialization progress."""
    progress_container = st.container()

    with progress_container:
        with st.spinner("Initializing database..."):
            results = {
                "connection": False,
                "tables": False,
                "verification": False,
                "stats": None,
                "errors": [],
            }

            # Step 1: Check connection
            st.info("🔗 Checking database connection...")
            try:
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                results["connection"] = True
                st.success("✓ Database connection successful")
            except Exception as e:
                results["errors"].append(f"Connection failed: {str(e)}")
                st.error(f"✗ Connection failed: {str(e)}")
                return

            # Step 2: Create tables
            st.info("📋 Creating database tables...")
            try:
                init_db()
                results["tables"] = True
                st.success("✓ Database tables created successfully")
            except Exception as e:
                results["errors"].append(f"Table creation failed: {str(e)}")
                st.error(f"✗ Table creation failed: {str(e)}")
                return

            # Step 3: Verify tables
            st.info("✓ Verifying database tables...")
            try:
                db = next(get_db())

                tables_to_check = {
                    "gse_series": GSESeries,
                    "mesh_term": MeshTerm,
                    "ingest_run": IngestRunModel,
                }

                all_exist = True
                for table_name, model in tables_to_check.items():
                    try:
                        db.query(model).limit(1).all()
                        st.caption(f"  ✓ Table '{table_name}' exists")
                    except Exception as e:
                        st.caption(f"  ✗ Table '{table_name}' missing: {str(e)}")
                        all_exist = False

                results["verification"] = all_exist
                if all_exist:
                    st.success("✓ All required tables exist")
                else:
                    st.error("✗ Some tables are missing")
                    return

                # Step 4: Get statistics
                st.info("📊 Getting database statistics...")
                try:
                    stats = {
                        "gse_count": db.query(GSESeries).count(),
                        "mesh_count": db.query(MeshTerm).count(),
                        "ingest_runs": db.query(IngestRunModel).count(),
                    }
                    results["stats"] = stats

                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("GSE Records", stats["gse_count"])
                    with col2:
                        st.metric("MeSH Terms", stats["mesh_count"])
                    with col3:
                        st.metric("Ingestion Runs", stats["ingest_runs"])

                    st.success("✓ Database statistics retrieved")

                except Exception as e:
                    st.error(f"✗ Failed to get database stats: {str(e)}")

                db.close()

            except Exception as e:
                results["errors"].append(f"Verification failed: {str(e)}")
                st.error(f"✗ Verification failed: {str(e)}")
                return

        # Final summary
        st.markdown("---")
        st.success("✅ **Database initialization complete!**")

        if results["stats"] and results["stats"]["gse_count"] == 0:
            st.info(
                "📥 **Next Steps:** Use the '🔍 Query Search' tab to start ingesting GEO datasets"
            )
        else:
            st.info("✓ Database is ready for search and queries")


def show_database_stats() -> None:
    """Display current database statistics."""
    try:
        db = next(get_db())

        gse_count = db.query(GSESeries).count()
        mesh_count = db.query(MeshTerm).count()
        ingest_runs = db.query(IngestRunModel).count()

        col1, col2, col3 = st.columns(3)

        with col1:
            st.metric("GSE Records", gse_count)

        with col2:
            st.metric("MeSH Terms", mesh_count)

        with col3:
            st.metric("Ingestion Runs", ingest_runs)

        db.close()

    except Exception as e:
        st.warning(f"⚠️ Could not fetch database stats: {str(e)}")
        st.caption("Database may still be initializing. Please refresh in a moment.")