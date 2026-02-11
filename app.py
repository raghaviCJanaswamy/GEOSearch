"""
Streamlit UI for GEOSearch.
Interactive search interface for GEO datasets.
"""
import logging
from datetime import datetime
from typing import Any

import streamlit as st
from sqlalchemy import distinct, func

from config import settings
from db import GSEMesh, GSESeries, IngestItem, IngestRun, MeshTerm, get_db
from search import HybridSearchEngine
from search.hybrid_search import make_snippet
from streamlit_ingest import show_ingestion_interface

# Configure logging
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)

# Page configuration
st.set_page_config(
    page_title="GEOSearch - AI-Powered GEO Dataset Search",
    page_icon="🔬",  # Microscope emoji (more compatible)
    layout="wide",
    initial_sidebar_state="expanded",
)


def get_cached_db():
    """Get database session with caching."""
    return next(get_db())


@st.cache_data(ttl=3600)
def get_filter_options():
    """Get available filter options from database."""
    db = get_cached_db()

    # Get unique organisms
    organisms_query = db.query(
        func.jsonb_array_elements_text(GSESeries.organisms).label("organism")
    ).distinct()
    organisms = [row[0] for row in organisms_query.limit(100).all() if row[0]]

    # Get unique tech types
    tech_types_query = db.query(distinct(GSESeries.tech_type)).filter(
        GSESeries.tech_type.isnot(None)
    )
    tech_types = [row[0] for row in tech_types_query.all() if row[0]]

    # Get date range
    date_range = db.query(
        func.min(GSESeries.submission_date),
        func.max(GSESeries.submission_date),
    ).first()

    return {
        "organisms": sorted(organisms),
        "tech_types": sorted(tech_types),
        "date_range": date_range,
    }


@st.cache_data(ttl=300)
def perform_search(
    query: str,
    organisms: list[str],
    tech_type: str | None,
    date_start: datetime | None,
    date_end: datetime | None,
    min_samples: int | None,
    use_semantic: bool,
    use_lexical: bool,
    use_mesh: bool,
    top_k: int,
) -> dict[str, Any]:
    """Perform search with caching."""
    db = get_cached_db()

    # Build filters
    filters = {}

    if organisms:
        filters["organisms"] = organisms

    if tech_type and tech_type != "All":
        filters["tech_type"] = tech_type

    if date_start or date_end:
        filters["date_range"] = {}
        if date_start:
            filters["date_range"]["start"] = date_start
        if date_end:
            filters["date_range"]["end"] = date_end

    if min_samples and min_samples > 0:
        filters["min_samples"] = min_samples

    # Perform search
    engine = HybridSearchEngine(db)
    results = engine.search(
        query=query,
        filters=filters,
        use_semantic=use_semantic,
        use_lexical=use_lexical,
        use_mesh=use_mesh,
        top_k=top_k,
    )

    return results


def render_mesh_term_badge(mesh_term: dict[str, Any]) -> None:
    """Render a MeSH term badge."""
    st.markdown(
        f'<span style="background-color: #e3f2fd; color: #1976d2; '
        f'padding: 2px 8px; border-radius: 12px; font-size: 0.85em; '
        f'margin-right: 4px;">{mesh_term["preferred_name"]}</span>',
        unsafe_allow_html=True,
    )


def render_result_card(result: dict[str, Any], query_terms: list[str]) -> None:
    """Render a search result card."""
    accession = result["accession"]
    title = result["title"]

    # Create expandable card
    with st.container():
        col1, col2 = st.columns([4, 1])

        with col1:
            # Title and accession
            st.markdown(f"### [{accession}]({result['geo_url']})")
            st.markdown(f"**{title}**")

            # Snippet
            if result.get("summary"):
                snippet = make_snippet(result["summary"], query_terms, max_length=300)
                st.caption(snippet)

        with col2:
            # Metadata
            if result.get("organisms"):
                st.caption(f"Organism: {', '.join(result['organisms'][:2])}")

            if result.get("tech_type"):
                st.caption(f"Tech: {result['tech_type']}")

            if result.get("sample_count"):
                st.caption(f"Samples: {result['sample_count']}")

            if result.get("submission_date"):
                date_str = result["submission_date"].split("T")[0] if "T" in result["submission_date"] else result["submission_date"]
                st.caption(f"Date: {date_str}")

        # MeSH terms
        if result.get("matched_mesh_terms"):
            st.markdown("**Matched MeSH Terms:**")
            mesh_html = " ".join([
                f'<span style="background-color: #e8f5e9; color: #2e7d32; '
                f'padding: 2px 8px; border-radius: 12px; font-size: 0.85em; '
                f'margin-right: 4px;">{term["preferred_name"]}</span>'
                for term in result["matched_mesh_terms"]
            ])
            st.markdown(mesh_html, unsafe_allow_html=True)

        # Expandable details
        with st.expander("Show details"):
            if result.get("overall_design"):
                st.markdown("**Overall Design:**")
                st.write(result["overall_design"])

            if result.get("platforms"):
                st.markdown("**Platforms:**")
                st.write(", ".join(result["platforms"]))

            if result.get("pubmed_ids"):
                st.markdown("**PubMed IDs:**")
                pmid_links = [
                    f"[{pmid}](https://pubmed.ncbi.nlm.nih.gov/{pmid}/)"
                    for pmid in result["pubmed_ids"]
                ]
                st.write(", ".join(pmid_links))

        st.divider()


def render_postgres_view() -> None:
    """Render PostgreSQL database view."""
    st.header("PostgreSQL Database View")

    db = get_cached_db()

    # Tabs for different views
    tab1, tab2, tab3, tab4 = st.tabs(["GSE Series", "MeSH Terms", "Ingestion Runs", "Statistics"])

    with tab1:
        st.subheader("GSE Series Records")

        # Get all GSE records
        gse_records = db.query(GSESeries).order_by(GSESeries.created_at.desc()).limit(100).all()

        if gse_records:
            st.write(f"Showing latest {len(gse_records)} records")

            for gse in gse_records:
                with st.expander(f"{gse.accession} - {gse.title[:100]}..."):
                    col1, col2 = st.columns(2)

                    with col1:
                        st.write("**Metadata:**")
                        st.write(f"- Accession: {gse.accession}")
                        st.write(f"- Organisms: {', '.join(gse.organisms) if gse.organisms else 'N/A'}")
                        st.write(f"- Tech Type: {gse.tech_type or 'N/A'}")
                        st.write(f"- Sample Count: {gse.sample_count or 'N/A'}")

                    with col2:
                        st.write("**Dates:**")
                        st.write(f"- Submission: {gse.submission_date.date() if gse.submission_date else 'N/A'}")
                        st.write(f"- Last Update: {gse.last_update_date.date() if gse.last_update_date else 'N/A'}")
                        st.write(f"- Created: {gse.created_at.strftime('%Y-%m-%d %H:%M')}")

                    if gse.summary:
                        st.write("**Summary:**")
                        st.write(gse.summary[:500] + ("..." if len(gse.summary) > 500 else ""))
        else:
            st.info("No GSE records found. Ingest some data first.")

    with tab2:
        st.subheader("MeSH Terms")

        mesh_terms = db.query(MeshTerm).order_by(MeshTerm.preferred_name).limit(100).all()

        if mesh_terms:
            st.write(f"Showing {len(mesh_terms)} MeSH terms")

            # Search box for MeSH terms
            search_term = st.text_input("Search MeSH terms:")

            if search_term:
                filtered_terms = [
                    term for term in mesh_terms
                    if search_term.lower() in term.preferred_name.lower()
                ]
            else:
                filtered_terms = mesh_terms[:20]

            for term in filtered_terms:
                with st.expander(f"{term.mesh_id}: {term.preferred_name}"):
                    st.write(f"**Descriptor UI:** {term.descriptor_ui}")
                    if term.entry_terms:
                        st.write("**Entry Terms (Synonyms):**")
                        for entry_term in term.entry_terms[:10]:
                            st.write(f"  - {entry_term}")
                    if term.tree_numbers:
                        st.write(f"**Tree Numbers:** {', '.join(term.tree_numbers)}")
        else:
            st.info("No MeSH terms found. Load MeSH data first: `python -m mesh.loader --sample`")

    with tab3:
        st.subheader("Ingestion Runs")

        runs = db.query(IngestRun).order_by(IngestRun.start_time.desc()).limit(20).all()

        if runs:
            for run in runs:
                status_color = {
                    "completed": "🟢",
                    "running": "🟡",
                    "failed": "🔴",
                    "partial": "🟠"
                }.get(run.status, "⚪")

                with st.expander(f"{status_color} Run #{run.id} - {run.query[:50]}"):
                    col1, col2 = st.columns(2)

                    with col1:
                        st.write(f"**Query:** {run.query}")
                        st.write(f"**Status:** {run.status}")
                        st.write(f"**Started:** {run.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
                        if run.end_time:
                            duration = (run.end_time - run.start_time).total_seconds()
                            st.write(f"**Duration:** {duration:.1f}s")

                    with col2:
                        st.write(f"**Total:** {run.total_count}")
                        st.write(f"**Success:** {run.success_count}")
                        st.write(f"**Errors:** {run.error_count}")
                        if run.total_count > 0:
                            success_rate = (run.success_count / run.total_count) * 100
                            st.write(f"**Success Rate:** {success_rate:.1f}%")

                    # Show failed items
                    if run.error_count > 0:
                        failed_items = db.query(IngestItem).filter(
                            IngestItem.run_id == run.id,
                            IngestItem.status == "failed"
                        ).limit(5).all()

                        if failed_items:
                            st.write("**Failed Items:**")
                            for item in failed_items:
                                st.write(f"  - {item.accession}: {item.error_message[:100]}")
        else:
            st.info("No ingestion runs found.")

    with tab4:
        st.subheader("Database Statistics")

        from sqlalchemy import func

        # GSE stats
        gse_count = db.query(func.count(GSESeries.accession)).scalar()
        st.metric("Total GSE Records", gse_count)

        if gse_count > 0:
            # Date range
            min_date, max_date = db.query(
                func.min(GSESeries.submission_date),
                func.max(GSESeries.submission_date)
            ).first()

            col1, col2, col3 = st.columns(3)

            with col1:
                if min_date:
                    st.metric("Earliest Record", str(min_date.date()))

            with col2:
                if max_date:
                    st.metric("Latest Record", str(max_date.date()))

            with col3:
                avg_samples = db.query(func.avg(GSESeries.sample_count)).scalar()
                if avg_samples:
                    st.metric("Avg Samples", f"{avg_samples:.1f}")

            # Top organisms
            st.write("**Top Organisms:**")
            org_query = db.query(
                func.jsonb_array_elements_text(GSESeries.organisms).label("organism"),
                func.count().label("count")
            ).group_by("organism").order_by(func.count().desc()).limit(10)

            for org, count in org_query:
                st.write(f"  - {org}: {count}")

            # Tech types
            st.write("**Technology Types:**")
            tech_query = db.query(
                GSESeries.tech_type,
                func.count()
            ).filter(GSESeries.tech_type.isnot(None)).group_by(
                GSESeries.tech_type
            ).order_by(func.count().desc()).all()

            for tech, count in tech_query:
                st.write(f"  - {tech}: {count}")

        # MeSH stats
        mesh_count = db.query(func.count(MeshTerm.mesh_id)).scalar()
        st.metric("Total MeSH Terms", mesh_count)

        # GSE-MeSH associations
        assoc_count = db.query(func.count(GSEMesh.id)).scalar()
        st.metric("GSE-MeSH Associations", assoc_count)


def render_milvus_view() -> None:
    """Render Milvus vector database view."""
    st.header("Milvus Vector Database View")

    try:
        from vector.milvus_store import MilvusStore

        store = MilvusStore()

        # Collection info
        st.subheader("Collection Information")

        col1, col2, col3 = st.columns(3)

        with col1:
            vector_count = store.count()
            st.metric("Total Vectors", vector_count)

        with col2:
            st.metric("Collection Name", store.collection_name)

        with col3:
            st.metric("Vector Dimension", store.dimension)

        # Connection info
        st.subheader("Connection Details")
        st.write(f"**Host:** {store.host}")
        st.write(f"**Port:** {store.port}")

        # Collection schema
        st.subheader("Collection Schema")
        try:
            schema = store.collection.schema
            st.write("**Fields:**")
            for field in schema.fields:
                st.write(f"  - {field.name} ({field.dtype})")
        except Exception as e:
            st.warning(f"Could not retrieve schema: {e}")

        # Sample query
        st.subheader("Test Query")

        test_query = st.text_input("Enter test query:", "cancer")

        if st.button("Run Test Search"):
            if test_query:
                with st.spinner("Searching..."):
                    from vector.embeddings import get_embedding_provider

                    embedding_provider = get_embedding_provider()
                    query_embedding = embedding_provider.embed_texts([test_query])[0]

                    results = store.search(
                        query_vector=query_embedding,
                        top_k=10
                    )

                    if results:
                        st.success(f"Found {len(results)} results")

                        for idx, result in enumerate(results, 1):
                            st.write(f"{idx}. **{result['accession']}** - Score: {result['score']:.4f}")
                    else:
                        st.info("No results found")

        # Collection stats
        if vector_count > 0:
            st.subheader("Statistics")
            st.write(f"**Vectors stored:** {vector_count:,}")
            st.write(f"**Estimated storage:** ~{(vector_count * store.dimension * 4 / 1024 / 1024):.2f} MB")

    except Exception as e:
        st.error(f"Error connecting to Milvus: {str(e)}")
        st.info("Make sure Milvus is running: `docker compose ps milvus`")


def main() -> None:
    """Main Streamlit application."""

    # Sidebar navigation - ALWAYS SHOW THIS FIRST
    st.sidebar.title("Navigation")
    page = st.sidebar.radio(
        "Select Page:",
        ["Search", "📥 Data Ingestion", "PostgreSQL View", "Milvus View"],
        index=0
    )

    # Data Ingestion - Always available (no database required for startup)
    if page == "📥 Data Ingestion":
        show_ingestion_interface()
        return
    
    # PostgreSQL View - Database required
    elif page == "PostgreSQL View":
        render_postgres_view()
        return
    
    # Milvus View - Milvus required
    elif page == "Milvus View":
        render_milvus_view()
        return

    # Original search page (requires database)
    # Header
    st.title("GEOSearch: AI-Powered GEO Dataset Search")
    st.markdown(
        "Search NCBI GEO datasets using semantic search, keyword matching, "
        "and MeSH terminology expansion."
    )

    # Sidebar - Filters
    st.sidebar.header("Search Filters")

    # Get filter options with error handling
    try:
        filter_options = get_filter_options()
    except Exception as e:
        st.error(f"Failed to load search filters: {str(e)}")
        st.warning(
            "**Database is not yet ready.** This is normal on first launch.\n\n"
            "**To get started:**\n"
            "1. Go to **📥 Data Ingestion** tab in the sidebar\n"
            "2. Enter a search query (e.g., 'cancer')\n"
            "3. Click **Start Ingestion**\n\n"
            "This will load data into the database and enable search."
        )
        return

    # Organism filter
    organisms = st.sidebar.multiselect(
        "Organisms",
        options=filter_options["organisms"],
        default=None,
        help="Filter by organism(s)",
    )

    # Technology type filter
    tech_type = st.sidebar.selectbox(
        "Technology Type",
        options=["All"] + filter_options["tech_types"],
        index=0,
        help="Filter by sequencing/array technology",
    )

    # Date range filter
    st.sidebar.subheader("Submission Date Range")
    date_min, date_max = filter_options["date_range"]

    if date_min and date_max:
        date_start = st.sidebar.date_input(
            "Start date",
            value=None,
            min_value=date_min.date() if date_min else None,
            max_value=date_max.date() if date_max else None,
        )

        date_end = st.sidebar.date_input(
            "End date",
            value=None,
            min_value=date_min.date() if date_min else None,
            max_value=date_max.date() if date_max else None,
        )
    else:
        date_start = None
        date_end = None

    # Sample count filter
    min_samples = st.sidebar.number_input(
        "Minimum Samples",
        min_value=0,
        value=0,
        step=1,
        help="Minimum number of samples in dataset",
    )

    # Search options
    st.sidebar.header("Search Options")

    use_semantic = st.sidebar.checkbox(
        "Use Semantic Search",
        value=True,
        help="Enable AI-powered semantic similarity search",
    )

    use_lexical = st.sidebar.checkbox(
        "Use Keyword Search",
        value=True,
        help="Enable traditional keyword matching",
    )

    use_mesh = st.sidebar.checkbox(
        "Use MeSH Expansion",
        value=True,
        help="Expand query with MeSH medical terminology",
    )

    top_k = st.sidebar.slider(
        "Number of Results",
        min_value=10,
        max_value=200,
        value=50,
        step=10,
        help="Maximum number of results to display",
    )

    # Main search area
    st.header("Search")

    # Search input
    query = st.text_input(
        "Enter your search query:",
        placeholder="e.g., breast cancer RNA-seq, single-cell diabetes, mouse liver expression",
        help="Enter keywords, phrases, or natural language queries",
    )

    # Search button
    col1, col2, col3 = st.columns([1, 1, 4])
    with col1:
        search_clicked = st.button("Search", type="primary", use_container_width=True)

    with col2:
        clear_cache = st.button("Clear Cache", use_container_width=True)
        if clear_cache:
            st.cache_data.clear()
            st.success("Cache cleared!")
            st.rerun()

    # Perform search
    if search_clicked and query:
        with st.spinner("Searching..."):
            try:
                results = perform_search(
                    query=query,
                    organisms=organisms,
                    tech_type=tech_type if tech_type != "All" else None,
                    date_start=datetime.combine(date_start, datetime.min.time()) if date_start else None,
                    date_end=datetime.combine(date_end, datetime.max.time()) if date_end else None,
                    min_samples=min_samples if min_samples > 0 else None,
                    use_semantic=use_semantic,
                    use_lexical=use_lexical,
                    use_mesh=use_mesh,
                    top_k=top_k,
                )

                metadata = results["metadata"]
                result_list = results["results"]

                # Display search metadata
                st.success(f"Found {metadata['total_results']} results")

                # Show MeSH expansion
                if use_mesh and metadata.get("mesh_terms"):
                    with st.expander("MeSH Terms Detected in Query", expanded=False):
                        st.markdown("Your query was expanded with these MeSH terms:")
                        mesh_html = " ".join([
                            f'<span style="background-color: #fff3e0; color: #e65100; '
                            f'padding: 4px 12px; border-radius: 12px; font-size: 0.9em; '
                            f'margin-right: 6px; display: inline-block; margin-bottom: 4px;">'
                            f'{term["preferred_name"]}</span>'
                            for term in metadata["mesh_terms"]
                        ])
                        st.markdown(mesh_html, unsafe_allow_html=True)

                # Display results
                if result_list:
                    st.markdown(f"### Results (1-{len(result_list)})")

                    query_terms = query.lower().split()

                    for idx, result in enumerate(result_list, 1):
                        render_result_card(result, query_terms)
                else:
                    st.warning("No results found. Try adjusting your search query or filters.")

            except Exception as e:
                st.error(f"Search failed: {str(e)}")
                logger.error(f"Search error: {e}", exc_info=True)

                # Show fallback message
                st.info(
                    "If Milvus is not running, semantic search will be disabled. "
                    "Make sure all services are running via docker-compose."
                )

    elif search_clicked and not query:
        st.warning("Please enter a search query.")

    # Footer
    st.sidebar.markdown("---")
    st.sidebar.markdown(
        """
        **About GEOSearch**

        GEOSearch combines:
        - Semantic AI search
        - Keyword matching
        - MeSH terminology

        Data from [NCBI GEO](https://www.ncbi.nlm.nih.gov/geo/)
        """
    )


if __name__ == "__main__":
    main()
