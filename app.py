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
from streamlit_analytics import show_analytics_dashboard
from streamlit_analysis import show_analysis_pipeline

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

# Reset persisted sidebar collapse state so left menu remains visible.
st.markdown(
    "<script>localStorage.removeItem('streamlit:sidebarState');</script>",
    unsafe_allow_html=True,
)

# Persistent left sidebar menu and compact filters
st.markdown("""
<style>
:root {
    --app-heading: #1a1a2e;
    --app-title: #14213d;
    --tab-border: #d4d8df;
    --tab-bar-bg: #eef1f5;
    --tab-text: #303744;
    --tab-hover: #e4e9f0;
    --tab-active-bg: #ffffff;
    --tab-active-border: #dce1e8;
    --muted-text: #5f6368;
}

@media (prefers-color-scheme: dark) {
    :root {
        --app-heading: #e7ecf4;
        --app-title: #ecf1fa;
        --tab-border: #343b46;
        --tab-bar-bg: #1f2631;
        --tab-text: #d4dbe8;
        --tab-hover: #2b3442;
        --tab-active-bg: #2a3341;
        --tab-active-border: #465164;
        --muted-text: #aab3c3;
    }
}

[data-testid="stSidebar"] {
    min-width: 220px !important;
    max-width: 260px !important;
    width: 240px !important;
    display: block !important;
    visibility: visible !important;
    transform: none !important;
}
[data-testid="stSidebar"] section[data-testid="stSidebarContent"] { padding: 12px 14px !important; }
[data-testid="stSidebar"][aria-expanded="false"] {
    min-width: 220px !important;
    max-width: 260px !important;
    width: 240px !important;
    transform: none !important;
}

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
/* Hide Streamlit top header bar */
header[data-testid="stHeader"] { display: none !important; }

.block-container {
    padding-top: 0.8rem !important;
    padding-bottom: 0 !important;
    padding-left: 2rem !important;
    padding-right: 2rem !important;
    max-width: 100% !important;
}

/* Keep sidebar fixed and non-collapsible */
[data-testid="stSidebarNav"] { display: none !important; }
[data-testid="collapsedControl"] { display: none !important; }
[data-testid="stSidebarCollapseButton"] { display: none !important; }

/* Expand main content to full width */
.appview-container .main .block-container { max-width: 100% !important; }

/* Global base font */
html, body, [class*="css"] { font-size: 15px !important; }
.stMarkdown p { font-size: 0.95rem !important; }
.stMarkdown h1 { font-size: 1.65rem !important; }
.stMarkdown h2 { font-size: 1.3rem !important; }
.stMarkdown h3 { font-size: 1.12rem !important; }
.stMarkdown h4, .stMarkdown h5 { font-size: 1rem !important; }

/* Sub-page headings (st.title → h1, st.header → h2) match main heading */
h1 { font-size: 1.65rem !important; font-weight: 800 !important; color: var(--app-heading) !important; letter-spacing: -0.4px !important; }
h2 { font-size: 1.3rem !important; font-weight: 700 !important; color: var(--app-heading) !important; }

/* Metrics */
[data-testid="stMetricLabel"] { font-size: 0.95rem !important; }
[data-testid="stMetricValue"] { font-size: 1.25rem !important; }

/* Inputs */
.stTextInput input, .stSelectbox select { font-size: 0.95rem !important; }
.stButton button { font-size: 0.95rem !important; padding: 0.32rem 0.9rem !important; }

/* Global submenu/tab bar styling across pages */
[data-testid="stTabs"] [role="tablist"] {
    gap: 0.2rem;
    border: 1px solid var(--tab-border);
    border-radius: 10px;
    background: var(--tab-bar-bg);
    padding: 0.3rem 0.35rem;
    margin-bottom: 1rem;
    overflow-x: auto;
    scrollbar-width: thin;
}

[data-testid="stTabs"] [role="tab"] {
    border: 1px solid transparent !important;
    background: transparent !important;
    border-bottom: 2px solid transparent !important;
    border-radius: 8px !important;
    padding: 0.32rem 0.65rem 0.46rem 0.65rem !important;
    margin-right: 0.08rem;
}

[data-testid="stTabs"] [role="tab"] p {
    font-size: 0.92rem !important;
    font-weight: 550 !important;
    color: var(--tab-text) !important;
    margin: 0 !important;
    white-space: nowrap;
}

[data-testid="stTabs"] [role="tab"]:hover {
    background: var(--tab-hover) !important;
}

[data-testid="stTabs"] [role="tab"][aria-selected="true"] {
    background: var(--tab-active-bg) !important;
    border-color: var(--tab-active-border) !important;
    border-bottom-color: #ff4b57 !important;
}

[data-testid="stTabs"] [role="tab"][aria-selected="true"] p {
    color: #ff4b57 !important;
}

/* Keep global radio styling neutral; menu radio is styled separately below */
.stRadio > div { gap: 6px !important; }

/* Tables */
.stDataFrame { font-size: 0.95rem !important; }

/* Main page title */
.geo-title { font-size: 1.2rem !important; font-weight: 700; margin: 0 0 4px 0; line-height: 1.2; }

/* Hide Deploy button and hamburger menu */
[data-testid="stToolbar"] { display: none !important; }
#MainMenu { display: none !important; }
footer { display: none !important; }
</style>
""", unsafe_allow_html=True)


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


def perform_search(
    query: str,
    organisms: list[str],
    tech_type: str | None,
    date_start: datetime | None,
    date_end: datetime | None,
    use_semantic: bool,
    use_lexical: bool,
    use_mesh: bool,
) -> dict[str, Any]:
    """Perform search with caching. Returns all matched results for client-side pagination."""
    db = SessionLocal()

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

    try:
        engine = HybridSearchEngine(db)
        results = engine.search(
            query=query,
            filters=filters,
            use_semantic=use_semantic,
            use_lexical=use_lexical,
            use_mesh=use_mesh,
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
    if st.button("🧬 Analyze", key=f"analyze_{accession}", help="Open in Expression Analysis pipeline"):
        st.session_state["analysis_gse_id"] = accession
        st.session_state["nav_page"] = "🧬 Analysis"
        st.rerun()



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


def render_footer_panel() -> None:
    """Render persistent footer panel."""
    st.markdown("---")
    st.markdown(
        """
<div style="padding: 0.35rem 0 0.65rem 0; color:var(--muted-text); font-size:0.9rem; line-height:1.4;">
  Data source: <a href="https://www.ncbi.nlm.nih.gov/geo/" target="_blank">NCBI GEO</a>
  &nbsp;&nbsp;|&nbsp;&nbsp;
  GEO Search: Semantic, lexical, and MeSH-enhanced discovery.
</div>
""",
        unsafe_allow_html=True,
    )


def main() -> None:
    """Main Streamlit application."""
    page_map = {
        "Search": "search",
        "Data Injestion": "ingest",
        "Ánalytics": "analytics",
        "Analysis": "analysis",
        "Settings": "docs",
    }
    reverse_page_map = {v: k for k, v in page_map.items()}
    legacy_page_map = {
        "🔍 Search": "search",
        "📥 Ingest": "ingest",
        "📊 Analytics": "analytics",
        "🧬 Analysis": "analysis",
        "📚 Docs": "docs",
    }

    nav_state = st.session_state.pop("nav_page", "search")
    if nav_state in legacy_page_map:
        nav_state = legacy_page_map[nav_state]
    nav_default = ["Search", "Data Injestion", "Ánalytics", "Analysis", "Settings"].index(
        reverse_page_map.get(nav_state, "Search")
    )

    st.markdown(
        """
<style>
[data-testid="stSidebar"] {
    min-width: 220px !important;
    max-width: 260px !important;
    width: 240px !important;
}
[data-testid="stSidebar"] section[data-testid="stSidebarContent"] {
    background: #e9ebef !important;
    border-radius: 16px;
    margin: 10px;
    padding: 12px 10px !important;
}

.left-menu-title {
    font-size: 1.25rem;
    font-weight: 700;
    color: #242a32;
    line-height: 1.2;
    margin: 0 0 0.4rem 0;
}

.left-menu-sub {
    font-size: 0.78rem;
    color: #5f6772;
    margin: 0.1rem 0 0.2rem 0;
}

[data-testid="stSidebar"] div[data-testid="stRadio"] {
    background: transparent !important;
    padding: 0 !important;
    margin: 0 !important;
}

[data-testid="stSidebar"] div[role="radiogroup"] > label {
    background: transparent;
    border-radius: 10px;
    padding: 8px 8px !important;
    margin: 4px 0 !important;
    color: #343a45 !important;
    border: 1px solid transparent;
    transition: background-color 120ms ease;
}

[data-testid="stSidebar"] div[role="radiogroup"] > label:hover {
    background: #dde1e8;
}

[data-testid="stSidebar"] div[role="radiogroup"] > label:has(input:checked) {
    background: #ff4b57 !important;
    color: #ffffff !important;
}

[data-testid="stSidebar"] div[role="radiogroup"] > label > div:first-child {
    display: none !important;
}

[data-testid="stSidebar"] div[role="radiogroup"] > label p {
    font-size: 1rem !important;
    font-weight: 600 !important;
    margin: 0 !important;
}

.footer-panel {
    padding: 0.3rem 0 0.55rem 0;
    color: var(--muted-text);
    font-size: 0.9rem;
}

@media (prefers-color-scheme: dark) {
    [data-testid="stSidebar"] section[data-testid="stSidebarContent"] {
        background: #1f2631 !important;
        border: 1px solid #343c48;
    }

    .left-menu-title {
        color: #e6ecf7;
    }

    .left-menu-sub {
        color: #aab4c5;
    }

    [data-testid="stSidebar"] div[role="radiogroup"] > label {
        color: #d4dbe8 !important;
    }

    [data-testid="stSidebar"] div[role="radiogroup"] > label:hover {
        background: #2b3442;
    }
}
</style>
""",
        unsafe_allow_html=True,
    )

    st.markdown(
        """
<div style="font-size:1.8rem;font-weight:800;color:var(--app-title);letter-spacing:-0.35px;margin:0 0 0.7rem 0;line-height:1.2;text-align:center;">
  🔬 GEO: AI Based Biomedical Datasets Discovery
</div>
""",
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.markdown('<div class="left-menu-title">🖥 Main Menu</div>', unsafe_allow_html=True)
        st.markdown("---")
        menu_choice = st.radio(
            "Main Menu",
            ["🔍 Search", "⇪ Data Injestion", "☰ Ánalytics", "🧬 Analysis", "⚙ Settings"],
            index=nav_default,
            label_visibility="collapsed",
            key="left_menu_nav",
        )
        st.markdown('<p class="left-menu-sub">Data from NCBI GEO</p>', unsafe_allow_html=True)

    page = page_map[menu_choice.split(" ", 1)[1]]

    if page == "docs":
        render_documentation()
        render_footer_panel()
        return

    if page == "ingest":
        show_ingestion_interface()
        render_footer_panel()
        return

    if page == "analytics":
        show_analytics_dashboard()
        render_footer_panel()
        return

    if page == "analysis":
        show_analysis_pipeline()
        render_footer_panel()
        return

    try:
        filter_options = get_filter_options()
    except Exception as e:
        st.error(f"Database not ready: {str(e)}")
        st.info("Go to **📥 Ingest** to load data first.")
        return

    date_min, date_max = filter_options["date_range"]

    col_q, col_s = st.columns([7, 1])
    with col_q:
        query = st.text_input(
            "query",
            placeholder="e.g., breast cancer RNA-seq, heart attack, single-cell diabetes",
            label_visibility="collapsed",
        )
    with col_s:
        search_clicked = st.button("Search", type="primary", use_container_width=True)

    # ── Inline filters ────────────────────────────────────────────────────────
    with st.expander("Filters & Search Options", expanded=False):
        fc1, fc2, fc3, fc4 = st.columns([2, 2, 1.5, 1.5])
        with fc1:
            organisms = st.multiselect(
                "Organism", options=filter_options["organisms"], default=None, placeholder="All")
        with fc2:
            tech_type = st.selectbox(
                "Technology", options=["All"] + filter_options["tech_types"], index=0)
        with fc3:
            date_start = st.date_input("From", value=None,
                min_value=date_min.date() if date_min else None,
                max_value=date_max.date() if date_max else None) if date_min and date_max else None
        with fc4:
            date_end = st.date_input("To", value=None,
                min_value=date_min.date() if date_min else None,
                max_value=date_max.date() if date_max else None) if date_min and date_max else None

        sc1, sc2, sc3 = st.columns(3)
        use_semantic = sc1.checkbox("Semantic search", value=True, help="AI vector similarity")
        use_lexical  = sc2.checkbox("Keyword search",  value=True, help="Full-text search")
        use_mesh     = sc3.checkbox("MeSH expansion",  value=True, help="Medical synonym expansion")

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
                    use_semantic=use_semantic,
                    use_lexical=use_lexical,
                    use_mesh=use_mesh,
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

        # Display search metadata — shown after pagination is computed below

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
            PAGE_SIZE = 20
            total = len(result_list)
            total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

            if "page" not in st.session_state:
                st.session_state.page = 1

            page = st.session_state.page
            start = (page - 1) * PAGE_SIZE
            end = min(start + PAGE_SIZE, total)

            # NCBI-style header + download buttons on the same row
            hdr_col, dl_col1, dl_col2 = st.columns([6, 1.2, 1.2])
            with hdr_col:
                st.markdown(
                    f'<div style="font-size:0.9em;color:#444;margin-bottom:6px;">'
                    f'Items: <b>{start+1}</b> to <b>{end}</b> of <b>{total}</b>'
                    f'&nbsp;&nbsp;|&nbsp;&nbsp;Page {page} of {total_pages}'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            with dl_col1:
                gse_list = "\n".join(r["accession"] for r in result_list)
                st.download_button(
                    label="⬇ GSE IDs (.txt)",
                    data=gse_list,
                    file_name=f"geosearch_{saved_query.replace(' ', '_')[:30]}.txt",
                    mime="text/plain",
                    use_container_width=True,
                    key="dl_txt",
                )
            with dl_col2:
                import csv, io
                csv_buf = io.StringIO()
                writer = csv.writer(csv_buf)
                writer.writerow(["accession", "title", "organisms", "tech_type", "sample_count", "submission_date", "geo_url"])
                for r in result_list:
                    writer.writerow([
                        r.get("accession", ""),
                        r.get("title", ""),
                        "; ".join(r.get("organisms") or []),
                        r.get("tech_type", ""),
                        r.get("sample_count", ""),
                        (r.get("submission_date") or "")[:10],
                        r.get("geo_url", ""),
                    ])
                st.download_button(
                    label="⬇ Full CSV",
                    data=csv_buf.getvalue(),
                    file_name=f"geosearch_{saved_query.replace(' ', '_')[:30]}.csv",
                    mime="text/csv",
                    use_container_width=True,
                    key="dl_csv",
                )

            # Top pagination controls
            pc1, pc2, pc3, pc4, pc5 = st.columns([1, 1, 4, 1, 1])
            with pc1:
                if st.button("« First", disabled=page <= 1, use_container_width=True, key="first_top"):
                    st.session_state.page = 1
                    st.rerun()
            with pc2:
                if st.button("‹ Prev", disabled=page <= 1, use_container_width=True, key="prev_top"):
                    st.session_state.page -= 1
                    st.rerun()
            with pc4:
                if st.button("Next ›", disabled=page >= total_pages, use_container_width=True, key="next_top"):
                    st.session_state.page += 1
                    st.rerun()
            with pc5:
                if st.button("Last »", disabled=page >= total_pages, use_container_width=True, key="last_top"):
                    st.session_state.page = total_pages
                    st.rerun()

            for result in result_list[start:end]:
                render_result_card(result)

            # Bottom pagination controls
            bc1, bc2, bc3, bc4, bc5 = st.columns([1, 1, 4, 1, 1])
            with bc1:
                if st.button("« First", disabled=page <= 1, use_container_width=True, key="first_bot"):
                    st.session_state.page = 1
                    st.rerun()
            with bc2:
                if st.button("‹ Prev", disabled=page <= 1, use_container_width=True, key="prev_bot"):
                    st.session_state.page -= 1
                    st.rerun()
            with bc3:
                st.markdown(
                    f'<div style="text-align:center;font-size:0.85em;color:#666;padding-top:8px;">'
                    f'Items {start+1}–{end} of {total}</div>',
                    unsafe_allow_html=True,
                )
            with bc4:
                if st.button("Next ›", disabled=page >= total_pages, use_container_width=True, key="next_bot"):
                    st.session_state.page += 1
                    st.rerun()
            with bc5:
                if st.button("Last »", disabled=page >= total_pages, use_container_width=True, key="last_bot"):
                    st.session_state.page = total_pages
                    st.rerun()
        else:
            st.warning("No results found. Try adjusting your search query or filters.")

    render_footer_panel()

 
if __name__ == "__main__":
    main()
