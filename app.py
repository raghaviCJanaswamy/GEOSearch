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
from db import GSESeries
from db.session import SessionLocal
from search import HybridSearchEngine
from streamlit_ingest import show_ingestion_interface

# Configure logging
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)

# Page configuration
st.set_page_config(
    page_title="GEO Datasets - Smart Search",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Compact sidebar CSS — tight within groups, breathing room between groups
st.markdown("""
<style>
[data-testid="stSidebar"] { min-width: 200px !important; max-width: 220px !important; }
[data-testid="stSidebar"] section[data-testid="stSidebarContent"] { padding: 10px 12px !important; }

/* Widget labels */
[data-testid="stSidebar"] label { font-size: 0.78rem !important; margin-bottom: 0 !important; }

/* Widgets — tight spacing within a group */
[data-testid="stSidebar"] .stSelectbox,
[data-testid="stSidebar"] .stMultiSelect,
[data-testid="stSidebar"] .stNumberInput,
[data-testid="stSidebar"] .stDateInput   { margin-bottom: 2px !important; margin-top: 0 !important; }

[data-testid="stSidebar"] .stSlider      { margin-bottom: 4px !important; }
[data-testid="stSidebar"] .stCheckbox    { margin-bottom: 0px !important; }
[data-testid="stSidebar"] .stRadio       { margin-bottom: 4px !important; }

/* Tighten widget label top margin */
[data-testid="stSidebar"] .stSelectbox > label,
[data-testid="stSidebar"] .stMultiSelect > label,
[data-testid="stSidebar"] .stDateInput > label { margin-top: 4px !important; margin-bottom: 0 !important; }

/* Input fields */
[data-testid="stSidebar"] input { font-size: 0.78rem !important; padding: 2px 6px !important; }

/* Radio */
[data-testid="stSidebar"] .stRadio > div { gap: 2px !important; }
[data-testid="stSidebar"] .stRadio label { font-size: 0.82rem !important; padding: 2px 0 !important; }

/* Checkbox */
[data-testid="stSidebar"] .stCheckbox { margin-bottom: 0 !important; padding-bottom: 0 !important; }
[data-testid="stSidebar"] .stCheckbox label { font-size: 0.78rem !important; }
[data-testid="stSidebar"] .stCheckbox > label { padding-top: 0 !important; padding-bottom: 0 !important; min-height: unset !important; }

/* HR — visible group separator with breathing room */
[data-testid="stSidebar"] hr { margin: 10px 0 !important; border-color: #ddd !important; }

/* Text */
[data-testid="stSidebar"] p  { font-size: 0.78rem !important; margin: 0 !important; line-height: 1.4 !important; }
[data-testid="stSidebar"] h3 { font-size: 1rem !important; margin: 0 0 8px 0 !important; }

/* Prevent sidebar p styles leaking into main content */
.main p.geo-heading { font-size: 1.6rem !important; font-weight: 700 !important; }

/* Remove default top padding on main content */
.block-container { padding-top: 1.5rem !important; padding-bottom: 0 !important; }

/* Remove sidebar top padding */
[data-testid="stSidebar"] section[data-testid="stSidebarContent"] > div:first-child { padding-top: 0.5rem !important; }
[data-testid="stSidebarNav"] { display: none !important; }

/* Main page title */
.geo-title { font-size: 1.4rem !important; font-weight: 700; margin: 0 0 6px 0; line-height: 1.2; }

/* Hide Deploy button and hamburger menu */
[data-testid="stToolbar"] { display: none !important; }
#MainMenu { display: none !important; }
footer { display: none !important; }
</style>
""", unsafe_allow_html=True)


@st.cache_data(ttl=3600)
def get_filter_options():
    """Get available filter options from database."""
    db = SessionLocal()

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

    db.close()
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
    db = SessionLocal()

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

    try:
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
    finally:
        db.close()

    return results


def render_mesh_term_badge(mesh_term: dict[str, Any]) -> None:
    """Render a MeSH term badge."""
    st.markdown(
        f'<span style="background-color: #e3f2fd; color: #1976d2; '
        f'padding: 2px 8px; border-radius: 12px; font-size: 0.85em; '
        f'margin-right: 4px;">{mesh_term["preferred_name"]}</span>',
        unsafe_allow_html=True,
    )


def render_result_card(result: dict[str, Any]) -> None:
    """Render a compact search result card."""
    accession = result["accession"]
    title = result["title"] or ""

    # Metadata pills
    meta_parts = []
    if result.get("organisms"):
        meta_parts.append(f"🧬 {', '.join(result['organisms'][:2])}")
    if result.get("tech_type") and result["tech_type"] != "unknown":
        meta_parts.append(f"⚙️ {result['tech_type']}")
    if result.get("sample_count"):
        meta_parts.append(f"📊 {result['sample_count']} samples")
    if result.get("submission_date"):
        date_str = result["submission_date"][:10]
        meta_parts.append(f"📅 {date_str}")
    if result.get("platforms"):
        gpls = " ".join(
            f'<a href="https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GPL{p}" target="_blank">GPL{p}</a>'
            for p in result["platforms"][:2]
        )
        meta_parts.append(f"🔬 {gpls}")

    meta_html = "&nbsp;&nbsp;|&nbsp;&nbsp;".join(meta_parts)

    # PubMed links
    pubmed_html = ""
    if result.get("pubmed_ids"):
        links = " ".join([
            f'<a href="https://pubmed.ncbi.nlm.nih.gov/{pmid}/" target="_blank">PMID:{pmid}</a>'
            for pmid in result["pubmed_ids"][:3]
        ])
        pubmed_html = f'<span style="font-size:0.82em;color:#666;">📄 {links}</span>'

    card_html = f"""
<div style="padding:10px 0 6px 0;border-bottom:1px solid #eee;">
  <div style="display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:4px;">
    <span>
      <a href="{result['geo_url']}" target="_blank"
         style="font-weight:700;font-size:1.05em;color:#1976d2;text-decoration:none;">
        {accession}
      </a>
      <span style="font-weight:600;font-size:0.95em;margin-left:10px;">{title}</span>
    </span>
  </div>
  <div style="font-size:0.82em;color:#555;margin:3px 0 4px 0;">{meta_html}</div>
  {pubmed_html}
</div>
"""
    st.markdown(card_html, unsafe_allow_html=True)



def render_documentation() -> None:
    """Render user documentation page."""
    st.title("📚 User Documentation")
    
    st.markdown("""
    Welcome to GEOSearch! This page provides quick access to all documentation.
    """)
    
    # Create tabs for documentation sections
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "🚀 Getting Started",
        "📊 Data Ingestion", 
        "🐳 Deployment",
        "🗄️ Database",
        "📋 Reference"
    ])
    
    with tab1:
        st.header("Getting Started")
        st.markdown("""
        - **[QUICKSTART.md](https://github.com/raghaviCJanaswamy/GEOSearch/blob/master/docs/QUICKSTART.md)** 
          - Quick setup and first search
        - **[FIRST_LAUNCH_GUIDE.md](https://github.com/raghaviCJanaswamy/GEOSearch/blob/master/docs/FIRST_LAUNCH_GUIDE.md)** 
          - Step-by-step first launch instructions
        - **[README.md](https://github.com/raghaviCJanaswamy/GEOSearch/blob/master/docs/README.md)** 
          - Project overview and features
        """)
    
    with tab2:
        st.header("Data Ingestion")
        st.markdown("""
        - **[STREAMLIT_INGESTION_QUICKREF.md](https://github.com/raghaviCJanaswamy/GEOSearch/blob/master/docs/STREAMLIT_INGESTION_QUICKREF.md)** 
          - Quick reference for the Data Ingestion UI
        - **[STREAMLIT_INGESTION_GUIDE.md](https://github.com/raghaviCJanaswamy/GEOSearch/blob/master/docs/STREAMLIT_INGESTION_GUIDE.md)** 
          - Complete ingestion guide
        - **[PRODUCTION_DATA_INGESTION.md](https://github.com/raghaviCJanaswamy/GEOSearch/blob/master/docs/PRODUCTION_DATA_INGESTION.md)** 
          - Production ingestion procedures
        """)
    
    with tab3:
        st.header("Docker Deployment")
        st.markdown("""
        - **[DOCKER_DEPLOYMENT_QUICK_REFERENCE.md](https://github.com/raghaviCJanaswamy/GEOSearch/blob/master/docs/DOCKER_DEPLOYMENT_QUICK_REFERENCE.md)** 
          - Essential Docker commands
        - **[DEPLOYMENT_GUIDE.md](https://github.com/raghaviCJanaswamy/GEOSearch/blob/master/docs/DEPLOYMENT_GUIDE.md)** 
          - Full deployment walkthrough
        - **[DOCKER_VISUAL_GUIDE.md](https://github.com/raghaviCJanaswamy/GEOSearch/blob/master/docs/DOCKER_VISUAL_GUIDE.md)** 
          - Visual guide to Docker architecture
        """)
    
    with tab4:
        st.header("Database Management")
        st.markdown("""
        - **[DATABASE_INITIALIZATION.md](https://github.com/raghaviCJanaswamy/GEOSearch/blob/master/docs/DATABASE_INITIALIZATION.md)** 
          - Database setup and initialization
        - **[MAINTENANCE_OPERATIONS_GUIDE.md](https://github.com/raghaviCJanaswamy/GEOSearch/blob/master/docs/MAINTENANCE_OPERATIONS_GUIDE.md)** 
          - Database maintenance tasks
        """)
    
    with tab5:
        st.header("Technical Reference")
        st.markdown("""
        - **[ARCHITECTURE_EXPLANATION.md](https://github.com/raghaviCJanaswamy/GEOSearch/blob/master/docs/ARCHITECTURE_EXPLANATION.md)** 
          - System architecture details
        - **[MESH_INTEGRATION_SUMMARY.md](https://github.com/raghaviCJanaswamy/GEOSearch/blob/master/docs/MESH_INTEGRATION_SUMMARY.md)** 
          - MeSH terminology integration
        - **[PROJECT_SUMMARY.md](https://github.com/raghaviCJanaswamy/GEOSearch/blob/master/docs/PROJECT_SUMMARY.md)** 
          - Project technical overview
        """)
    
    st.divider()
    
    # Quick links section
    st.subheader("Quick Help")
    col1, col2, col3 = st.columns(3)
    
    with col1:
        if st.button("🔍 How to Search", key="help_search", use_container_width=True):
            st.info("Go to the **Search** tab to find GEO datasets using semantic search, keywords, or MeSH terms.")
    
    with col2:
        if st.button("📥 How to Ingest Data", key="help_ingest", use_container_width=True):
            st.info("Go to the **Data Ingestion** tab to add new datasets from NCBI GEO.")
    
    with col3:
        if st.button("🐳 Docker Help", key="help_docker", use_container_width=True):
            st.info("See DOCKER_DEPLOYMENT_QUICK_REFERENCE.md for common Docker commands.")


def main() -> None:
    """Main Streamlit application."""

    # Sidebar navigation
    st.sidebar.markdown("### 🔬 GEOSearch")
    page = st.sidebar.radio(
        "nav", ["🔍 Search", "📥 Ingest", "📚 Docs"],
        index=0, label_visibility="collapsed",
    )

    if page == "📚 Docs":
        render_documentation()
        return

    if page == "📥 Ingest":
        show_ingestion_interface()
        return

    try:
        filter_options = get_filter_options()
    except Exception as e:
        st.error(f"Database not ready: {str(e)}")
        st.info("Go to **📥 Ingest** to load data first.")
        return

    date_min, date_max = filter_options["date_range"]

    st.sidebar.markdown("---")
    organisms = st.sidebar.multiselect(
        "Organism", options=filter_options["organisms"], default=None, placeholder="All")
    tech_type = st.sidebar.selectbox(
        "Technology", options=["All"] + filter_options["tech_types"], index=0)
    if date_min and date_max:
        date_start = st.sidebar.date_input("From", value=None,
            min_value=date_min.date(), max_value=date_max.date())
        date_end = st.sidebar.date_input("To", value=None,
            min_value=date_min.date(), max_value=date_max.date())
    else:
        date_start = date_end = None

    st.sidebar.markdown("---")
    use_semantic = st.sidebar.checkbox("Semantic", value=True, help="AI vector similarity")
    use_lexical  = st.sidebar.checkbox("Keyword",  value=True, help="Full-text search")
    use_mesh     = st.sidebar.checkbox("MeSH",     value=True, help="Medical synonym expansion")
    top_k = st.sidebar.slider("Results", min_value=10, max_value=200, value=50, step=10)

    st.header("GEO Dataset Smart Search")

    col_q, col_s = st.columns([7, 1])
    with col_q:
        query = st.text_input(
            "query",
            placeholder="e.g., breast cancer RNA-seq, heart attack, single-cell diabetes",
            label_visibility="collapsed",
        )
    with col_s:
        search_clicked = st.button("Search", type="primary", use_container_width=True)

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
                    min_samples=None,
                    use_semantic=use_semantic,
                    use_lexical=use_lexical,
                    use_mesh=use_mesh,
                    top_k=top_k,
                )
                st.session_state["_results"] = results
                st.session_state["_search_query"] = query
                st.session_state["_use_mesh"] = use_mesh
                st.session_state.page = 1
            except Exception as e:
                st.error(f"Search failed: {str(e)}")
                logger.error(f"Search error: {e}", exc_info=True)
                st.info(
                    "If Milvus is not running, semantic search will be disabled. "
                    "Make sure all services are running via docker-compose."
                )

    elif search_clicked and not query:
        st.warning("Please enter a search query.")

    # Render stored results (persists across pagination reruns)
    if st.session_state.get("_results"):
        results = st.session_state["_results"]
        saved_query = st.session_state.get("_search_query", "")
        saved_use_mesh = st.session_state.get("_use_mesh", True)
        metadata = results["metadata"]
        result_list = results["results"]

        # Display search metadata
        st.caption(f"Found {metadata['total_results']} results")

        # Show MeSH expansion
        if saved_use_mesh and metadata.get("mesh_terms"):
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

        if result_list:
            PAGE_SIZE = 10
            total = len(result_list)
            total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

            if "page" not in st.session_state:
                st.session_state.page = 1

            page = st.session_state.page
            start = (page - 1) * PAGE_SIZE
            end = min(start + PAGE_SIZE, total)

            st.markdown(
                f"**{total} results** &nbsp;·&nbsp; page {page} of {total_pages}",
                unsafe_allow_html=True,
            )

            for result in result_list[start:end]:
                render_result_card(result)

            # Pagination controls
            col_prev, col_info, col_next = st.columns([1, 4, 1])
            with col_prev:
                if st.button("← Prev", disabled=page <= 1, use_container_width=True):
                    st.session_state.page -= 1
                    st.rerun()
            with col_info:
                st.markdown(
                    f'<div style="text-align:center;font-size:0.85em;color:#666;">'
                    f'Showing {start+1}–{end} of {total}</div>',
                    unsafe_allow_html=True,
                )
            with col_next:
                if st.button("Next →", disabled=page >= total_pages, use_container_width=True):
                    st.session_state.page += 1
                    st.rerun()
        else:
            st.warning("No results found. Try adjusting your search query or filters.")

    st.sidebar.markdown("---")
    st.sidebar.caption("Data from [NCBI GEO](https://www.ncbi.nlm.nih.gov/geo/)")


if __name__ == "__main__":
    main()
