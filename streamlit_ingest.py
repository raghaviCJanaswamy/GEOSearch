"""
Streamlit UI components for data ingestion.
Provides interface to run GEO data ingestion from the Streamlit app.
"""
import logging
import time
from datetime import datetime
from typing import Any

import streamlit as st
from sqlalchemy import text
from sqlalchemy.orm import Session

from config import settings
from db import IngestRun, get_db, engine, init_db
from db.session import SessionLocal
from db.models import GSESeries, MeshTerm, IngestRun as IngestRunModel
from geo_ingest.ingest_pipeline import IngestionPipeline
from geo_ingest.parser import GEOParser
from streamlit_ingest_mesh import show_mesh_loader
from vector.embeddings import get_embedding_provider
from vector.milvus_store import MilvusStore

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
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
        ["🔍 Query Search", "📂 File Import", "🏷️ MeSH Tagger", "📋 Ingestion History", "⚙️ Configuration", "🗄️ Database"]
    )

    with tab1:
        show_query_ingestion()

    with tab2:
        show_file_import()

    with tab3:
        if db_available:
            show_mesh_tagger()
        else:
            st.info("MeSH tagger will be available once database is ready.")

    with tab4:
        if db_available:
            show_ingestion_history()
        else:
            st.info("Ingestion history will be available once database is ready.")

    with tab5:
        show_ingestion_config()

    with tab6:
        show_database_initialization()


ORGANISM_OPTIONS = [
    "Any",
    "Homo sapiens",
    "Mus musculus",
    "Rattus norvegicus",
    "Drosophila melanogaster",
    "Caenorhabditis elegans",
    "Danio rerio",
    "Arabidopsis thaliana",
    "Saccharomyces cerevisiae",
]


def show_query_ingestion() -> None:
    """Show interface for ingesting by search query."""
    st.subheader("Search and Ingest")

    cq_col1, cq_col2 = st.columns([3, 1])
    with cq_col1:
        query = st.text_input(
            "Search Query",
            placeholder="e.g., 'breast cancer RNA-seq' or 'melanoma microarray'",
            help="Enter your NCBI search query",
        )
    with cq_col2:
        organism = st.selectbox("Species", options=ORGANISM_OPTIONS, index=0, key="cq_organism")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        retmax = st.number_input(
            "Max Results", min_value=1, max_value=10000, value=50, step=10,
        )
    with col2:
        skip_existing = st.checkbox("Skip Existing", value=True)
    with col3:
        mindate = st.date_input("From Date", value=None)
    with col4:
        maxdate = st.date_input("To Date", value=None)

    mindate_str = mindate.strftime("%Y/%m/%d") if mindate else None
    maxdate_str = maxdate.strftime("%Y/%m/%d") if maxdate else None

    if st.button("🚀 Start Ingestion", type="primary", use_container_width=True):
        if not query:
            st.error("Please enter a search query")
            return

        try:
            db_test = next(get_db())
            db_test.close()
        except Exception as e:
            st.error(f"Cannot start ingestion: Database not ready\n\nError: {str(e)}")
            return

        full_query = query
        if organism != "Any":
            full_query = f"{query} AND {organism}[Organism]"

        ingest_with_progress(
            query=full_query,
            retmax=retmax,
            mindate=mindate_str,
            maxdate=maxdate_str,
            skip_existing=skip_existing,
        )


def _parse_txt_records(content: str) -> list[dict]:
    """Parse gds_result_summary.txt content into raw record dicts."""
    import re
    blocks = re.split(r"\n(?=\d+\.\s)", content.strip())
    records = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.splitlines()
        title = re.sub(r"^\d+\.\s*", "", lines[0]).strip() if lines else ""
        summary = ""
        for line in lines[1:]:
            if line.startswith("(Submitter supplied)"):
                summary = re.sub(r"^\(Submitter supplied\)\s*", "", line).strip().rstrip(" more...")
                break
        organism = ""
        for line in lines:
            m = re.match(r"Organism:\s*(.+)", line)
            if m:
                organism = m.group(1).strip()
                break
        tech_type_raw = ""
        for line in lines:
            m = re.match(r"Type:\s*(.+)", line)
            if m:
                tech_type_raw = m.group(1).strip()
                break
        platforms, sample_count = [], None
        for line in lines:
            m = re.match(r"Platform:\s*(.+)", line)
            if m:
                ps = m.group(1).strip()
                for part in ps.split():
                    if part.startswith("GPL") and part[3:].isdigit():
                        platforms.append(int(part[3:]))
                cm = re.search(r"(\d+)\s+Samples?", ps, re.IGNORECASE)
                if cm:
                    sample_count = int(cm.group(1))
                break
        accession = ""
        for line in lines:
            m = re.search(r"Accession:\s*(GSE\d+)", line)
            if m:
                accession = m.group(1).strip()
                break
        if not accession:
            continue
        records.append({
            "accession": accession,
            "title": title,
            "summary": summary,
            "overall_design": "",
            "organisms": [organism] if organism else [],
            "tech_type_raw": tech_type_raw,
            "platforms": platforms,
            "sample_count": sample_count,
            "submission_date": None,
            "pubmed_ids": [],
        })
    return records


def show_file_import() -> None:
    """Upload a GEO summary txt file and import into DB + Milvus."""
    st.subheader("Import from File")
    st.caption("Upload a GEO summary text file (e.g. gds_result_summary.txt) downloaded from NCBI.")

    uploaded = st.file_uploader("Choose a .txt file", type=["txt"], key="geo_txt_upload")
    skip_existing = st.checkbox("Skip records already in database", value=True, key="file_import_skip")

    if uploaded is None:
        st.info("Download a summary file from NCBI GEO search results, then upload it here.")
        return

    content = uploaded.read().decode("utf-8", errors="replace")
    records = _parse_txt_records(content)

    import re
    total_blocks = len(re.split(r"\n(?=\d+\.\s)", content.strip()))
    gsm_count = total_blocks - len(records)

    if not records:
        st.error("No records found in file — check the file format.")
        return

    st.success(
        f"Found **{total_blocks} total entries** in `{uploaded.name}`:  \n"
        f"- **{len(records)} GSE Series** (study-level datasets — these will be imported)  \n"
        f"- **{gsm_count} GSM Samples** (individual samples — skipped, already covered by their GSE Series)"
    )

    with st.expander("Preview records", expanded=False):
        for r in records[:5]:
            st.markdown(f"**{r['accession']}** — {r['title'][:80]}")
            st.caption(f"Organism: {', '.join(r['organisms']) or 'N/A'} | Samples: {r['sample_count']} | Type: {r['tech_type_raw'][:50]}")

    # MeSH tagging option
    with SessionLocal() as _db:
        mesh_loaded = _db.execute(text("SELECT COUNT(*) FROM mesh_term")).scalar() or 0
    tag_during_import = st.checkbox(
        "🏷️ Auto-tag with MeSH terms during import",
        value=mesh_loaded > 0,
        disabled=mesh_loaded == 0,
        help="Tags each record with MeSH descriptors as it is imported. Requires MeSH terms to be loaded first. Adds ~0.1s per record.",
    )
    if mesh_loaded == 0:
        st.caption("⚠️ MeSH terms not loaded — load them first via the MeSH Tagger tab to enable this option.")

    if st.button("Import into Database", type="primary", use_container_width=True, key="file_import_btn"):
        try:
            db = next(get_db())
        except Exception as e:
            st.error(f"Database not ready: {e}")
            return

        parser = GEOParser()
        embedding_provider = get_embedding_provider()
        vector_store = MilvusStore()

        run = IngestRun(
            query=f"txt_import:{uploaded.name}",
            start_time=datetime.utcnow(),
            status="running",
            run_metadata={"source_file": uploaded.name, "total": len(records)},
        )
        db.add(run)
        db.commit()

        # Filter existing
        skipped = 0
        to_process = records
        if skip_existing:
            existing = {
                row[0] for row in db.query(GSESeries.accession)
                .filter(GSESeries.accession.in_([r["accession"] for r in records]))
                .all()
            }
            to_process = [r for r in records if r["accession"] not in existing]
            skipped = len(records) - len(to_process)

        progress_bar = st.progress(0)
        status_text  = st.empty()

        success, errors, mesh_tagged = 0, 0, 0
        parsed_for_embed: list[tuple[str, dict]] = []
        total = len(to_process)

        # Initialise MeSH matcher once if tagging enabled
        mesh_matcher = None
        if tag_during_import and mesh_loaded > 0:
            from mesh.matcher import MeSHMatcher
            mesh_matcher = MeSHMatcher(db)

        mc1, mc2, mc3, mc4 = st.columns(4)
        success_ph = mc1.empty()
        error_ph   = mc2.empty()
        skip_ph    = mc3.empty()
        mesh_ph    = mc4.empty()

        for i, raw in enumerate(to_process, 1):
            status_text.info(f"Processing {raw['accession']} ({i}/{total})...")
            progress_bar.progress(int(i / max(total, 1) * 80))
            try:
                parsed = parser.parse_gse_metadata(raw)
                if not parsed:
                    errors += 1
                    continue
                db.merge(GSESeries(**parsed))
                db.commit()
                parsed_for_embed.append((raw["accession"], parsed))
                success += 1

                # MeSH tag inline
                if mesh_matcher:
                    from db.models import GSEMesh
                    try:
                        for match in mesh_matcher.match_gse(raw["accession"], 0.3):
                            db.merge(GSEMesh(
                                accession=raw["accession"],
                                mesh_id=match["mesh_id"],
                                source="auto",
                                confidence=match["confidence"],
                            ))
                            mesh_tagged += 1
                        db.commit()
                    except Exception:
                        db.rollback()

            except Exception as e:
                db.rollback()
                errors += 1
                logger.error(f"Failed {raw['accession']}: {e}")

            success_ph.metric("Stored",      success)
            error_ph.metric("Errors",        errors)
            skip_ph.metric("Skipped",        skipped)
            mesh_ph.metric("MeSH Tags",      mesh_tagged)

        # Embeddings
        if parsed_for_embed:
            status_text.info(f"Generating embeddings for {len(parsed_for_embed)} records...")
            try:
                texts = [parser.prepare_embedding_text(p) for _, p in parsed_for_embed]
                embeddings = embedding_provider.embed_texts(texts)
                vectors = [
                    {"accession": acc, "embedding": emb}
                    for (acc, _), emb in zip(parsed_for_embed, embeddings)
                    if emb is not None
                ]
                if vectors:
                    vector_store.upsert(vectors)
            except Exception as e:
                st.warning(f"Embeddings failed: {e}")

        progress_bar.progress(100)

        run.end_time = datetime.utcnow()
        run.total_count = len(records)
        run.success_count = success
        run.error_count = errors
        run.status = "completed" if errors == 0 else "partial"
        db.commit()
        db.close()

        if success > 0:
            mesh_msg = f", **{mesh_tagged:,}** MeSH tags applied" if mesh_matcher else ""
            status_text.success(f"Done! **{success:,}** records imported, **{errors}** errors, **{skipped}** skipped{mesh_msg}.")
        else:
            status_text.warning(f"No new records imported. {errors} errors, {skipped} skipped.")


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
    st.subheader("System Configuration")

    from config import settings
    from sqlalchemy import text

    def _row(label, value, status=None):
        status_badge = f" &nbsp; `{status}`" if status else ""
        return f"| **{label}** | {value}{status_badge} |"

    def _section(title, rows):
        st.markdown(f"##### {title}")
        table = "| Setting | Value |\n|---|---|\n" + "\n".join(rows)
        st.markdown(table, unsafe_allow_html=True)
        st.markdown("")

    # ── Embedding Model ──────────────────────────────────────────────────────
    try:
        from vector.embeddings import get_embedding_provider
        ep = get_embedding_provider()
        dim = ep.get_dimension()
        model_name = getattr(ep, "model_name", type(ep).__name__)
        provider_type = type(ep).__name__.replace("EmbeddingProvider", "")
        model_status = "✅ Loaded"
    except Exception as e:
        dim, model_name, provider_type = "—", str(e)[:60], "—"
        model_status = "❌ Failed"

    _section("🧠 Embedding Model", [
        _row("Provider", provider_type),
        _row("Model", model_name),
        _row("Vector Dimensions", f"**{dim}**"),
        _row("Status", model_status),
    ])

    # ── Vector Database (Milvus) ──────────────────────────────────────────────
    try:
        from vector.milvus_store import MilvusStore
        ms = MilvusStore()
        vec_count = ms.count()
        collection = getattr(settings, "milvus_collection_name", "gse_embeddings")
        milvus_host = getattr(settings, "milvus_host", "milvus")
        milvus_port = getattr(settings, "milvus_port", 19530)
        milvus_status = "✅ Connected"
    except Exception:
        vec_count = 0
        collection = getattr(settings, "milvus_collection_name", "gse_embeddings")
        milvus_host = getattr(settings, "milvus_host", "milvus")
        milvus_port = getattr(settings, "milvus_port", 19530)
        milvus_status = "❌ Disconnected"

    _section("🗄️ Vector Database (Milvus)", [
        _row("Host", f"{milvus_host}:{milvus_port}"),
        _row("Collection", collection),
        _row("Vectors Stored", f"{vec_count:,}"),
        _row("Vector Dimension", f"**{dim}**"),
        _row("Status", milvus_status),
    ])

    # ── PostgreSQL ────────────────────────────────────────────────────────────
    try:
        db = next(get_db())
        pg_host = getattr(settings, "postgres_host", "postgres")
        pg_port = getattr(settings, "postgres_port", 5432)
        pg_db   = getattr(settings, "postgres_db", "geosearch")
        gse_count  = db.execute(text("SELECT COUNT(*) FROM gse_series")).scalar() or 0
        mesh_count = db.execute(text("SELECT COUNT(*) FROM mesh_term")).scalar() or 0
        tag_count  = db.execute(text("SELECT COUNT(*) FROM gse_mesh")).scalar() or 0
        od_count   = db.execute(text(
            "SELECT COUNT(*) FROM gse_series WHERE overall_design IS NOT NULL AND overall_design != ''"
        )).scalar() or 0
        od_pct = round(od_count / gse_count * 100, 1) if gse_count > 0 else 0
        tagged = db.execute(text("SELECT COUNT(DISTINCT accession) FROM gse_mesh")).scalar() or 0
        tagged_pct = round(tagged / gse_count * 100, 1) if gse_count > 0 else 0
        pg_status = "✅ Connected"
    except Exception:
        pg_host, pg_port, pg_db = "postgres", 5432, "geosearch"
        gse_count = mesh_count = tag_count = od_count = tagged = 0
        od_pct = tagged_pct = 0
        pg_status = "❌ Disconnected"

    _section("🐘 PostgreSQL Database", [
        _row("Host", f"{pg_host}:{pg_port}"),
        _row("Database", pg_db),
        _row("GSE Series", f"{gse_count:,}"),
        _row("MeSH Terms Loaded", f"{mesh_count:,}"),
        _row("MeSH Tags (gse_mesh)", f"{tag_count:,}"),
        _row("Series MeSH Tagged", f"{tagged:,} ({tagged_pct}%)"),
        _row("With overall_design", f"{od_count:,} ({od_pct}%)"),
        _row("Status", pg_status),
    ])

    # ── Search Settings ───────────────────────────────────────────────────────
    semantic_top_k = getattr(settings, "semantic_top_k", 100)
    lexical_top_k  = getattr(settings, "lexical_top_k", 100)

    _section("🔍 Search Settings", [
        _row("Semantic Top-K", semantic_top_k),
        _row("Lexical Top-K", lexical_top_k),
        _row("Min Score (primary)", "0.65"),
        _row("Min Score (fallback)", "0.60"),
        _row("MeSH Expansion Cap", "Top 5 terms"),
        _row("RRF k-parameter", "60"),
        _row("MeSH Boost per tag", "+0.1"),
        _row("MeSH Boost cap", "+0.5 max"),
    ])

    # ── NCBI API ──────────────────────────────────────────────────────────────
    rate = 10.0 if getattr(settings, "ncbi_api_key", None) else 3.0
    _section("🔬 NCBI API", [
        _row("Email", settings.ncbi_email or "Not set"),
        _row("API Key", "✅ Set" if getattr(settings, "ncbi_api_key", None) else "❌ Not set"),
        _row("Rate Limit", f"{rate} req/s"),
        _row("Base URL", "eutils.ncbi.nlm.nih.gov"),
    ])

    # ── LLM / RAG ─────────────────────────────────────────────────────────────
    ollama_host  = getattr(settings, "ollama_host", "ollama")
    ollama_port  = getattr(settings, "ollama_port", 11434)
    ollama_model = getattr(settings, "ollama_model", "llama3")
    try:
        import requests as _req
        r = _req.get(f"http://{ollama_host}:{ollama_port}/api/tags", timeout=3)
        ollama_status = "✅ Running"
    except Exception:
        ollama_status = "❌ Offline"

    _section("🤖 LLM / RAG (Ollama)", [
        _row("Host", f"{ollama_host}:{ollama_port}"),
        _row("Model", ollama_model),
        _row("Status", ollama_status),
    ])

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

def show_mesh_tagger() -> None:
    """MeSH auto-tagger tab — tag all ingested GSE records with MeSH terms."""
    st.subheader("🏷️ MeSH Auto-Tagger")
    st.caption(
        "Automatically tags every GSE series in the database with matching MeSH descriptors "
        "based on title, summary, and overall design text."
    )

    from sqlalchemy import text
    from db.session import SessionLocal

    # ── Status ──────────────────────────────────────────────────────────────
    with SessionLocal() as db:
        total_series = db.execute(text("SELECT COUNT(*) FROM gse_series")).scalar() or 0
        total_mesh   = db.execute(text("SELECT COUNT(*) FROM mesh_term")).scalar() or 0
        tagged       = db.execute(text("SELECT COUNT(DISTINCT accession) FROM gse_mesh WHERE source='auto'")).scalar() or 0
        total_tags   = db.execute(text("SELECT COUNT(*) FROM gse_mesh WHERE source='auto'")).scalar() or 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("GSE Series in DB",    f"{total_series:,}")
    c2.metric("MeSH Terms Loaded",   f"{total_mesh:,}")
    c3.metric("Series Tagged",       f"{tagged:,}",    f"{tagged/total_series*100:.1f}%" if total_series else "0%")
    c4.metric("Total MeSH Tags",     f"{total_tags:,}")

    if total_mesh == 0:
        st.error("❌ No MeSH terms loaded. Go to **File Import → Load MeSH XML** first before tagging.")
        return

    if total_series == 0:
        st.warning("No GSE series in database. Import data first.")
        return

    st.markdown("---")

    # ── Options ─────────────────────────────────────────────────────────────
    col1, col2 = st.columns(2)
    with col1:
        confidence = st.slider(
            "Confidence threshold",
            min_value=0.1, max_value=0.9, value=0.3, step=0.05,
            help="Minimum match confidence. Lower = more tags, higher = stricter matching.",
        )
    with col2:
        overwrite = st.checkbox(
            "Overwrite existing tags",
            value=False,
            help="If checked, existing auto-tags are deleted and regenerated.",
        )

    untagged = total_series - tagged
    if untagged > 0:
        st.info(
            f"**{untagged:,}** series not yet tagged. "
            f"At ~7 records/sec this will take approximately "
            f"**{untagged // 7 // 3600}h {(untagged // 7 % 3600) // 60}m** — "
            f"runs as a **background process** so the UI stays responsive."
        )
    else:
        st.success(f"✅ All {total_series:,} series are tagged. Use **Overwrite** to re-tag with new settings.")

    # ── Check if background process is running ────────────────────────────────
    import subprocess, os, signal

    pid_file = "/tmp/mesh_tagger.pid"
    log_file = "/tmp/mesh_tagger.log"

    def _get_running_pid():
        try:
            pid = int(open(pid_file).read().strip())
            os.kill(pid, 0)   # raises if process is gone
            return pid
        except Exception:
            return None

    pid = _get_running_pid()
    is_running = pid is not None

    col_btn1, col_btn2 = st.columns(2)
    start_btn = col_btn1.button(
        "▶ Start Background Tagger",
        type="primary",
        use_container_width=True,
        key="run_mesh_tagger",
        disabled=is_running,
    )
    stop_btn = col_btn2.button(
        "⏹ Stop Tagger",
        use_container_width=True,
        key="stop_mesh_tagger",
        disabled=not is_running,
    )

    if stop_btn and is_running:
        try:
            os.kill(pid, signal.SIGTERM)
            os.remove(pid_file)
            st.warning("⏹ Tagger stopped.")
            st.rerun()
        except Exception as e:
            st.error(f"Could not stop: {e}")

    if start_btn and not is_running:
        # Write background script to /tmp and launch it
        script = f"""
import sys, os, time
sys.path.insert(0, '/app')
os.environ.setdefault('PYTHONPATH', '/app')

from db.session import SessionLocal
from mesh.matcher import MeSHMatcher
from db.models import GSESeries, GSEMesh

log = open('{log_file}', 'w', buffering=1)

def p(msg):
    log.write(msg + '\\n')
    log.flush()

p('START')
with SessionLocal() as db:
    accessions = [r[0] for r in db.query(GSESeries.accession).all()]
    total = len(accessions)
    p(f'TOTAL {{total}}')
    matcher = MeSHMatcher(db)
    done = 0
    tags = 0
    t0 = time.time()
    BATCH = 500
    for i in range(0, total, BATCH):
        batch = accessions[i:i+BATCH]
        for acc in batch:
            if {'True' if overwrite else 'False'}:
                db.query(GSEMesh).filter(GSEMesh.accession==acc, GSEMesh.source=='auto').delete()
            for m in matcher.match_gse(acc, {confidence}):
                db.merge(GSEMesh(accession=acc, mesh_id=m['mesh_id'], source='auto', confidence=m['confidence']))
                tags += 1
        db.commit()
        done = min(i + BATCH, total)
        elapsed = time.time() - t0
        rate = done / elapsed if elapsed > 0 else 1
        eta = int((total - done) / rate)
        p(f'PROGRESS {{done}} {{total}} {{tags}} {{int(elapsed)}} {{eta}}')
    p(f'DONE {{tags}}')
log.close()
"""
        with open("/tmp/mesh_tagger_worker.py", "w") as f:
            f.write(script)

        proc = subprocess.Popen(
            ["python", "/tmp/mesh_tagger_worker.py"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        with open(pid_file, "w") as f:
            f.write(str(proc.pid))
        st.success(f"✅ Background tagger started (PID {proc.pid}). Monitor progress below.")
        time.sleep(1)
        st.rerun()

    # ── Live progress monitor ─────────────────────────────────────────────────
    st.markdown("---")
    if is_running or os.path.exists(log_file):
        st.subheader("📊 Tagger Progress")

        try:
            lines = open(log_file).readlines()
        except Exception:
            lines = []

        progress_lines = [l.strip() for l in lines if l.startswith("PROGRESS")]
        done_lines     = [l.strip() for l in lines if l.startswith("DONE")]
        total_line     = next((l.strip() for l in lines if l.startswith("TOTAL")), None)

        total_rec = int(total_line.split()[1]) if total_line else total_series

        if done_lines:
            final_tags = int(done_lines[-1].split()[1])
            st.success(f"✅ Tagging complete! **{final_tags:,}** tag associations created across **{total_rec:,}** series.")
            if os.path.exists(pid_file):
                os.remove(pid_file)
        elif progress_lines:
            last = progress_lines[-1].split()
            # PROGRESS done total tags elapsed eta
            done_n, total_n, tags_n, elapsed_n, eta_n = int(last[1]), int(last[2]), int(last[3]), int(last[4]), int(last[5])
            pct  = done_n / total_n if total_n else 0
            rate = done_n / elapsed_n if elapsed_n > 0 else 0
            eta_str = f"{eta_n // 3600}h {(eta_n % 3600) // 60}m {eta_n % 60}s" if eta_n > 3600 else f"{eta_n // 60}m {eta_n % 60}s"

            st.progress(pct)
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Processed",    f"{done_n:,} / {total_n:,}")
            c2.metric("Tags Created", f"{tags_n:,}")
            c3.metric("Rate",         f"{rate:.1f} rec/s")
            c4.metric("ETA",          eta_str)
            st.caption(f"⏱ Elapsed: {elapsed_n // 60}m {elapsed_n % 60}s")
        elif is_running:
            st.info("⏳ Tagger starting up…")

        if is_running:
            st.caption("Auto-refreshing every 10 seconds…")
            time.sleep(10)
            st.rerun()

    # ── Sample preview ───────────────────────────────────────────────────────
    if tagged > 0:
        st.markdown("---")
        st.subheader("Sample Tagged Records")
        with SessionLocal() as db:
            rows = db.execute(text("""
                SELECT gm.accession, mt.preferred_name, gm.confidence
                FROM gse_mesh gm
                JOIN mesh_term mt ON gm.mesh_id = mt.mesh_id
                WHERE gm.source = 'auto'
                ORDER BY gm.confidence DESC
                LIMIT 20
            """)).fetchall()
        if rows:
            import pandas as pd
            df = pd.DataFrame(rows, columns=["Accession", "MeSH Term", "Confidence"])
            df["Confidence"] = df["Confidence"].map(lambda x: f"{x:.2f}" if x else "N/A")
            st.dataframe(df, use_container_width=True, hide_index=True)


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