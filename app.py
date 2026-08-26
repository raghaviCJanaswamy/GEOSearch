"""
Streamlit UI for GEOSearch.
Interactive search interface for GEO datasets.
"""
import csv
import io
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st
from sqlalchemy import String, distinct, func, or_

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

# Absolute path to annotations.csv (shipped with the repo under data/)
_ANNOTATIONS_CSV = Path(__file__).parent / "data" / "annotations.csv"
_OUTPUT_DIR = Path(__file__).parent / "output"


@st.cache_data(show_spinner=False)
def load_annotations() -> dict[str, dict]:
    """Load data/annotations.csv into memory keyed by GSE accession.

    Returns an empty dict if the file is missing.
    """
    if not _ANNOTATIONS_CSV.exists():
        return {}
    annotations: dict[str, dict] = {}
    with open(_ANNOTATIONS_CSV, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            acc = row.get("gse", "").strip().strip('"')
            if acc:
                annotations[acc] = row
    return annotations


def export_result_descriptions(query: str, result_list: list[dict]) -> Path:
    """Compare search result GSE IDs against annotations.csv and write a
    markdown descriptions file to the output/ directory.

    Returns the Path of the written file.
    """
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    slug = query.strip().replace(" ", "_")[:60] if query.strip() else "filtered"
    out_path = _OUTPUT_DIR / f"{slug}_descriptions.md"

    annotations = load_annotations()

    in_annotations: list[tuple[dict, dict]] = []   # (result, annotation_row)
    not_in_annotations: list[dict] = []

    for r in result_list:
        acc = r.get("accession", "")
        if acc in annotations:
            in_annotations.append((r, annotations[acc]))
        else:
            not_in_annotations.append(r)

    lines: list[str] = []
    lines.append(f"# GEOSearch Result Descriptions")
    lines.append(f"")
    lines.append(f"**Query:** `{query}`  ")
    lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}  ")
    lines.append(f"**Total results:** {len(result_list)}  ")
    lines.append(f"**Matched in annotations.csv:** {len(in_annotations)}  ")
    lines.append(f"**Not in annotations.csv:** {len(not_in_annotations)}  ")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Section 1 — datasets with annotation metadata
    lines.append(f"## Results with Annotations ({len(in_annotations)})")
    lines.append("")
    for rank, (r, ann) in enumerate(in_annotations, start=1):
        acc = r.get("accession", "")
        title = r.get("title") or ann.get("one_sentence_summary") or ""
        organism = r.get("organisms") or []
        organism_str = ", ".join(organism) if isinstance(organism, list) else str(organism)
        tech = r.get("tech_type") or ""
        sample_count = r.get("sample_count") or ""
        submission_date = (r.get("submission_date") or "")[:10]

        # Annotation fields
        primary_condition = ann.get("primary_condition") or ""
        primary_modality = ann.get("primary_modality") or ""
        resolution = ann.get("resolution") or ""
        primary_material = ann.get("primary_material") or ""
        one_sentence = ann.get("one_sentence_summary") or ""
        curation_status = ann.get("curation_status") or ""

        lines.append(f"### {rank}. [{acc}](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={acc}) — {title}")
        lines.append("")
        lines.append(f"| Field | Value |")
        lines.append(f"|---|---|")
        lines.append(f"| Organism | {organism_str} |")
        lines.append(f"| Technology | {tech} |")
        lines.append(f"| Samples | {sample_count} |")
        lines.append(f"| Submitted | {submission_date} |")
        lines.append(f"| Primary condition | {primary_condition} |")
        lines.append(f"| Modality | {primary_modality} |")
        lines.append(f"| Resolution | {resolution} |")
        lines.append(f"| Material | {primary_material} |")
        lines.append(f"| Curation status | {curation_status} |")
        lines.append("")
        if one_sentence:
            lines.append(f"**Summary:** {one_sentence}")
            lines.append("")
        lines.append("")

    lines.append("---")
    lines.append("")

    # Section 2 — datasets not in annotations.csv
    lines.append(f"## Results Not in annotations.csv ({len(not_in_annotations)})")
    lines.append("")
    if not_in_annotations:
        lines.append("| Rank | Accession | Title | Organism | Technology | Samples |")
        lines.append("|---|---|---|---|---|---|")
        for rank, r in enumerate(not_in_annotations, start=len(in_annotations) + 1):
            acc = r.get("accession", "")
            title = (r.get("title") or "")[:80]
            organism = r.get("organisms") or []
            organism_str = ", ".join(organism[:2]) if isinstance(organism, list) else str(organism)
            tech = r.get("tech_type") or ""
            sample_count = r.get("sample_count") or ""
            lines.append(f"| {rank} | [{acc}](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={acc}) | {title} | {organism_str} | {tech} | {sample_count} |")
    else:
        lines.append("All results were found in annotations.csv.")
    lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


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
    try:
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
    finally:
        db.close()


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


def perform_filtered_search(
    organisms: list[str],
    tech_type: str | None,
    date_start: datetime | None,
    date_end: datetime | None,
) -> dict[str, Any]:
    """Return GEO records matching only the selected filters."""
    db = SessionLocal()

    try:
        query = db.query(GSESeries)

        if organisms:
            organism_conditions = [
                func.cast(GSESeries.organisms, String).like(f"%{org}%")
                for org in organisms
            ]
            query = query.filter(or_(*organism_conditions))

        if tech_type and tech_type != "All":
            query = query.filter(GSESeries.tech_type == tech_type)

        if date_start:
            query = query.filter(GSESeries.submission_date >= date_start)

        if date_end:
            query = query.filter(GSESeries.submission_date <= date_end)

        records = (
            query.order_by(GSESeries.submission_date.desc().nullslast(), GSESeries.accession.asc())
            .all()
        )

        results = []
        for record in records:
            item = record.to_dict()
            item["geo_url"] = f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={record.accession}"
            item["matched_mesh_terms"] = []
            results.append(item)

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

        return {
            "results": results,
            "metadata": {
                "query": "",
                "expanded_query": "",
                "mesh_terms": [],
                "semantic_count": 0,
                "lexical_count": 0,
                "total_results": len(results),
                "filters_applied": filters,
            },
        }
    finally:
        db.close()


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



COMMON_ORGANISMS = [
    "Homo sapiens",
    "Mus musculus",
    "Rattus norvegicus",
    "Danio rerio",
    "Drosophila melanogaster",
    "Caenorhabditis elegans",
    "Saccharomyces cerevisiae",
    "Arabidopsis thaliana",
    "Gallus gallus",
    "Sus scrofa",
    "Bos taurus",
    "Macaca mulatta",
    "Pan troglodytes",
]

STUDY_TYPES = [
    "Any",
    "Expression profiling by high throughput sequencing",
    "Expression profiling by array",
    "Genome binding/occupancy profiling by high throughput sequencing",
    "Methylation profiling by high throughput sequencing",
    "Non-coding RNA profiling by high throughput sequencing",
    "Single-cell RNA sequencing",
    "Whole genome sequencing",
    "Exome sequencing",
    "Other",
]


def _run_script_with_progress(cmd: list[str]) -> None:
    """Launch a script subprocess and stream output with progress metrics."""
    import re as _re
    import time as _time

    with st.expander("Command", expanded=False):
        st.code(" ".join(cmd), language="bash")

    status_label = st.empty()
    prog_bar     = st.progress(0)
    col_a, col_b, col_c, col_d = st.columns(4)
    metric_total    = col_a.empty()
    metric_fetched  = col_b.empty()
    metric_ingested = col_c.empty()
    metric_eta      = col_d.empty()
    log_area        = st.empty()

    status_label.info("⏳ Starting…")
    metric_total.metric("Total records", "—")
    metric_fetched.metric("Fetched", "0")
    metric_ingested.metric("Ingested (PG)", "0")
    metric_eta.metric("ETA", "—")

    log_lines = []
    total = fetched = ingested = 0
    start_ts = _time.time()

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1)
        for raw in proc.stdout:
            line = raw.rstrip()
            if not line:
                continue
            log_lines.append(line)
            log_area.code("\n".join(log_lines[-60:]), language="")

            # Total record count — fetch_geo and bulk_ingest
            m = _re.search(r"Total.*?records.*?:\s*([\d,]+)", line)
            if m:
                total = int(m.group(1).replace(",", ""))
                metric_total.metric("Total records", f"{total:,}")
                status_label.info(f"⏳ Fetching {total:,} records…")

            # backfill_fast: "Starting backfill: 1266 records"
            m = _re.search(r"Starting backfill:\s*([\d,]+)\s+records", line)
            if m:
                total = int(m.group(1).replace(",", ""))
                metric_total.metric("Total records", f"{total:,}")
                status_label.info(f"⏳ Backfilling {total:,} records…")

            m = _re.search(r"UIDs fetched:\s*([\d,]+)\s*/\s*([\d,]+)", line)
            if m:
                fetched = int(m.group(1).replace(",", ""))
                metric_fetched.metric("Fetched", f"{fetched:,}")

            m = _re.search(r"Accessions resolved:\s*([\d,]+)", line)
            if m:
                fetched = int(m.group(1).replace(",", ""))
                metric_fetched.metric("Fetched", f"{fetched:,}")
                status_label.info(f"⏳ Ingesting {fetched:,} records…")

            # fetch_geo progress: "Progress: N/N PG: N"
            m = _re.search(r"Progress:\s*([\d,]+)/([\d,]+).*?PG:\s*([\d,]+)", line)
            if m:
                done_n  = int(m.group(1).replace(",", ""))
                total_n = int(m.group(2).replace(",", ""))
                ingested = int(m.group(3).replace(",", ""))
                metric_fetched.metric("Fetched", f"{done_n:,}")
                metric_ingested.metric("Ingested (PG)", f"{ingested:,}")
                if total_n > 0:
                    pct = done_n / total_n
                    prog_bar.progress(min(pct, 1.0))
                    elapsed = _time.time() - start_ts
                    if pct > 0:
                        eta_s = int(elapsed / pct * (1 - pct))
                        metric_eta.metric("ETA", f"{eta_s//60}m {eta_s%60}s")

            # backfill_fast progress: "Progress: N/N (xx%) | updated=N no_data=N errors=N"
            m = _re.search(r"Progress:\s*([\d,]+)/([\d,]+)\s*\([\d.]+%\).*?updated=([\d,]+)", line)
            if m:
                done_n  = int(m.group(1).replace(",", ""))
                total_n = int(m.group(2).replace(",", ""))
                ingested = int(m.group(3).replace(",", ""))
                metric_fetched.metric("Fetched", f"{done_n:,}")
                metric_ingested.metric("Ingested (PG)", f"{ingested:,}")
                if total_n > 0:
                    pct = done_n / total_n
                    prog_bar.progress(min(pct, 1.0))
                    elapsed = _time.time() - start_ts
                    if pct > 0:
                        eta_s = int(elapsed / pct * (1 - pct))
                        metric_eta.metric("ETA", f"{eta_s//60}m {eta_s%60}s")

            # backfill_fast commit: "Committed 100 updates [100/1266]"
            m = _re.search(r"Committed\s+([\d,]+)\s+updates\s+\[(\d+)/(\d+)\]", line)
            if m:
                ingested = int(m.group(2))
                run_total = int(m.group(3))
                if total == 0:
                    total = run_total
                    metric_total.metric("Total records", f"{total:,}")
                metric_ingested.metric("Ingested (PG)", f"{ingested:,}")
                if run_total > 0:
                    pct = ingested / run_total
                    prog_bar.progress(min(pct, 1.0))
                    elapsed = _time.time() - start_ts
                    if pct > 0:
                        eta_s = int(elapsed / pct * (1 - pct))
                        metric_eta.metric("ETA", f"{eta_s//60}m {eta_s%60}s")

            # bulk_ingest commit: "Committed N updates [N/N]" (older format)
            m = _re.search(r"Committed\s+([\d,]+).*?\[(\d+)/(\d+)\]", line)
            if m and "updates" not in line:
                ingested = int(m.group(2))
                run_total = int(m.group(3))
                metric_ingested.metric("Ingested (PG)", f"{ingested:,}")
                if run_total > 0:
                    pct = ingested / run_total
                    prog_bar.progress(min(pct, 1.0))
                    elapsed = _time.time() - start_ts
                    if pct > 0:
                        eta_s = int(elapsed / pct * (1 - pct))
                        metric_eta.metric("ETA", f"{eta_s//60}m {eta_s%60}s")

            m = _re.search(r"PostgreSQL:\s*([\d,]+)\s+upserted", line)
            if m:
                ingested = int(m.group(1).replace(",", ""))
                metric_ingested.metric("Ingested (PG)", f"{ingested:,}")

        proc.wait()
        elapsed_total = int(_time.time() - start_ts)
        if proc.returncode == 0:
            prog_bar.progress(1.0)
            status_label.success(
                f"✅ Done — {ingested:,} records ingested in "
                f"{elapsed_total//60}m {elapsed_total%60}s"
            )
            metric_eta.metric("ETA", "Done")
        else:
            status_label.error(f"❌ Script failed (exit {proc.returncode})")
    except Exception as exc:
        status_label.error(f"❌ Error: {exc}")


def show_ncbi_download() -> None:
    """UI for downloading GEO records from NCBI — 3 options."""
    st.title("⬇️ NCBI GEO Download")
    st.markdown(
        "Three methods to fetch GEO Series records from NCBI into PostgreSQL + Milvus. "
        "See [ncbidownload-process.md](docs/ncbidownload-process.md) for full details."
    )

    st.markdown("---")

    def _filter_widgets(key_prefix: str) -> tuple[list[str], str, str, str, bool]:
        """Shared organism/date/study-type filter widgets. Returns (organisms, date_start, date_end, ncbi_query, date_ok)."""
        fc1, fc2 = st.columns([2, 2])
        with fc1:
            sel_orgs = st.multiselect("Organism(s)", options=COMMON_ORGANISMS,
                                      default=["Homo sapiens"], key=f"{key_prefix}_orgs")
            custom_org = st.text_input("Custom organism (optional)",
                                       placeholder="e.g. Oryza sativa", key=f"{key_prefix}_custom")
        with fc2:
            sel_type = st.selectbox("Study type", options=STUDY_TYPES, index=0, key=f"{key_prefix}_type")
            sel_kw   = st.text_input("Additional keyword (optional)",
                                     placeholder="e.g. single-cell, ATAC-seq", key=f"{key_prefix}_kw")

        dc1, dc2 = st.columns(2)
        with dc1:
            ds = st.text_input("From date (YYYY/MM/DD)", value="2000/01/01", key=f"{key_prefix}_ds")
        with dc2:
            de = st.text_input("To date (YYYY/MM/DD)", value=datetime.today().strftime("%Y/%m/%d"),
                               key=f"{key_prefix}_de")

        date_ok = True
        for lbl, val in [("From date", ds), ("To date", de)]:
            try:
                datetime.strptime(val.strip(), "%Y/%m/%d")
            except ValueError:
                st.error(f"❌ {lbl} must be YYYY/MM/DD (got: `{val}`)")
                date_ok = False

        all_orgs = list(dict.fromkeys(sel_orgs + ([custom_org.strip()] if custom_org.strip() else [])))
        parts = []
        if all_orgs:
            parts.append("(" + " OR ".join(f'"{o}"[Organism]' for o in all_orgs) + ")")
        parts.append("gse[Entry Type]")
        if sel_type != "Any":
            parts.append(f'"{sel_type}"[DataSet Type]')
        if sel_kw.strip():
            parts.append(sel_kw.strip())
        query = " AND ".join(parts)

        with st.expander("NCBI query preview", expanded=False):
            st.code(query, language="")

        return all_orgs, ds.strip(), de.strip(), query, date_ok

    scripts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")

    tab1, tab2, tab3 = st.tabs([
        "⚡ Option 1 — Bulk EFetch (Fastest)",
        "🔬 Option 2 — Per-record SOFT (All Fields)",
        "🔄 Option 3 — Backfill Enrichment",
    ])

    # ── Tab 1: Bulk EFetch ────────────────────────────────────────────────────
    with tab1:
        st.markdown(
            "**~300 records/sec** · Fetches 500 records per API call · Best for initial full-corpus load  \n"
            "Fields: `title`, `summary`, `organism`, `tech_type`, `platforms`, `sample_count`  \n"
            "Missing: `overall_design`, `status`, `contributors`, `citations` — run **Option 3** after."
        )
        st.markdown("---")
        all_orgs1, ds1, de1, query1, date_ok1 = _filter_widgets("bulk")

        ac1, ac2 = st.columns(2)
        with ac1:
            limit1   = st.number_input("Max records", 100, 500_000, 200_000, 10_000, key="bulk_limit")
        with ac2:
            no_embed1 = st.checkbox("Skip Milvus embedding (DB only — faster)", value=False, key="bulk_noembed")
        force1 = st.checkbox("Re-ingest existing records", value=False, key="bulk_force")

        if st.button("🚀 Start Bulk Ingest", type="primary", key="bulk_start"):
            if not all_orgs1:
                st.error("Select at least one organism.")
            elif not date_ok1:
                st.error("Fix date format errors above.")
            else:
                cmd = [sys.executable, os.path.join(scripts_dir, "bulk_ingest_geo.py"),
                       "--date-start", ds1, "--date-end", de1,
                       "--limit", str(int(limit1)),
                       "--query", query1]
                for o in all_orgs1:
                    cmd += ["--organism", o]
                if no_embed1:
                    cmd.append("--no-embed")
                if force1:
                    cmd.append("--force")
                _run_script_with_progress(cmd)

    # ── Tab 2: Per-record SOFT ────────────────────────────────────────────────
    with tab2:
        st.markdown(
            "**~2-5 records/sec** · One SOFT text API call per record · Gets **all 7 fields** in one pass  \n"
            "Fields: `title`, `summary`, `overall_design`, `status`, `contributors`, `citations`, `organism`  \n"
            "Best for targeted date ranges or incremental monthly updates."
        )
        st.markdown("---")
        all_orgs2, ds2, de2, query2, date_ok2 = _filter_widgets("soft")

        bc1, bc2, bc3 = st.columns(3)
        with bc1:
            max_rec2   = st.number_input("Max records", 100, 500_000, 10_000, 1_000, key="soft_max")
        with bc2:
            workers2   = st.number_input("Workers", 1, 20, 6, 1, key="soft_workers")
        with bc3:
            rate_lim2  = st.number_input("Rate limit (req/s)", 1.0, 10.0, 5.0, 0.5, key="soft_rate")
        force2 = st.checkbox("Re-ingest existing records", value=False, key="soft_force")

        if st.button("🚀 Start Per-record Download", type="primary", key="soft_start"):
            if not all_orgs2:
                st.error("Select at least one organism.")
            elif not date_ok2:
                st.error("Fix date format errors above.")
            else:
                cmd = [sys.executable, os.path.join(scripts_dir, "fetch_geo_homo_sapiens.py"),
                       "--date-start", ds2, "--date-end", de2,
                       "--max-records", str(int(max_rec2)),
                       "--workers", str(int(workers2)),
                       "--rate-limit", str(float(rate_lim2)),
                       "--query", query2]
                for o in all_orgs2:
                    cmd += ["--organism", o]
                if force2:
                    cmd.append("--force")
                _run_script_with_progress(cmd)

    # ── Tab 3: Backfill ───────────────────────────────────────────────────────
    with tab3:
        st.markdown(
            "**~5-10 records/sec** · Enriches records already in DB with missing fields  \n"
            "Fills: `overall_design`, `status`, `contributors`, `citations (pubmed_ids)`  \n"
            "Run this after **Option 1** to enrich the full corpus. Can be resumed if interrupted."
        )
        st.markdown("---")

        cc1, cc2, cc3 = st.columns(3)
        with cc1:
            bf_workers   = st.number_input("Workers", 1, 20, 8, 1, key="bf_workers")
        with cc2:
            bf_rate      = st.number_input("Rate limit (req/s)", 1.0, 10.0, 5.0, 0.5, key="bf_rate")
        with cc3:
            bf_limit     = st.number_input("Max records (0 = all)", 0, 500_000, 0, 10_000, key="bf_limit")

        bf_all    = st.checkbox("Re-process all records (even already enriched)", value=False, key="bf_all")
        bf_dryrun = st.checkbox("Dry run (fetch but do not write to DB)", value=False, key="bf_dry")

        st.info(
            "Tip: leave Max records = 0 to process all records missing `overall_design` / "
            "`status` / `contributors`. The script skips already-enriched records automatically."
        )

        if st.button("🚀 Start Backfill", type="primary", key="bf_start"):
            cmd = [sys.executable, os.path.join(scripts_dir, "backfill_fast.py"),
                   "--workers", str(int(bf_workers)),
                   "--rate-limit", str(float(bf_rate))]
            if bf_limit > 0:
                cmd += ["--limit", str(int(bf_limit))]
            if bf_all:
                cmd.append("--all")
            if bf_dryrun:
                cmd.append("--dry-run")
            _run_script_with_progress(cmd)


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
        - **QUICKSTART.md** — Quick setup and first search
        - **README.md** — Project overview and features
        """)

    with tab2:
        st.header("Data Ingestion")
        st.markdown("""
        - **[ncbidownload-process.md](docs/ncbidownload-process.md)** — NCBI download process details
        - **[search-architecture.md](docs/search-architecture.md)** — Search pipeline architecture
        """)

    with tab3:
        st.header("Docker Deployment")
        st.markdown("""
        - **[DOCKER_DEPLOYMENT_IMPLEMENTATION_COMPLETE.md](docs/DOCKER_DEPLOYMENT_IMPLEMENTATION_COMPLETE.md)** — Docker deployment guide
        - Use `docker-compose up` to start all services.
        """)

    with tab4:
        st.header("Database Management")
        st.markdown("""
        - Run `python -m geo_ingest.ingest_pipeline init` to initialise the database.
        - See **[MESH_FULL_DATABASE_SUMMARY.md](MESH_FULL_DATABASE_SUMMARY.md)** for MeSH database details.
        """)

    with tab5:
        st.header("Technical Reference")
        st.markdown("""
        - **[docs/search-architecture.md](docs/search-architecture.md)** — System architecture
        - **[docs/MESH_TAGGING_CHAPTER.md](docs/MESH_TAGGING_CHAPTER.md)** — MeSH tagging details
        - **[PROJECT_SUMMARY.md](PROJECT_SUMMARY.md)** — Project technical overview
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
        "Data Ingestion": "ingest",
        "Analytics": "analytics",
        "Analysis": "analysis",
        "NCBI Downloads": "download",
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

    # ── Admin authentication ───────────────────────────────────────────────────
    _admin_password = os.environ.get("ADMIN_PASSWORD", "geoadmin")
    if _admin_password == "geoadmin":
        logger.warning("ADMIN_PASSWORD env var not set — using insecure default. Set ADMIN_PASSWORD in your environment.")
    if "is_admin" not in st.session_state:
        st.session_state["is_admin"] = False

    nav_state = st.session_state.pop("nav_page", "search")
    if nav_state in legacy_page_map:
        nav_state = legacy_page_map[nav_state]

    # Admin users see all 6 items; general users see 4 (no ingest/download)
    if st.session_state["is_admin"]:
        nav_labels = ["Search", "Analytics", "Analysis", "Data Ingestion", "NCBI Downloads", "Settings"]
    else:
        nav_labels = ["Search", "Analytics", "Analysis"]
        if nav_state in ("ingest", "download", "docs"):
            nav_state = "search"

    nav_default = nav_labels.index(reverse_page_map.get(nav_state, "Search")) if reverse_page_map.get(nav_state, "Search") in nav_labels else 0

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

    # Build display labels (with icons) from nav_labels
    _icon_map = {
        "Search": "🔍 Search",
        "Data Ingestion": "⇪ Data Ingestion",
        "Analytics": "☰ Analytics",
        "Analysis": "🧬 Analysis",
        "NCBI Downloads": "⬇️ NCBI Downloads",
        "Settings": "⚙ Settings",
    }
    nav_display = [_icon_map[lbl] for lbl in nav_labels]

    with st.sidebar:
        st.markdown('<div class="left-menu-title">🖥 Main Menu</div>', unsafe_allow_html=True)
        st.markdown("---")
        menu_choice = st.radio(
            "Main Menu",
            nav_display,
            index=nav_default,
            label_visibility="collapsed",
            key="left_menu_nav",
        )
        st.markdown('<p class="left-menu-sub">Data from NCBI GEO</p>', unsafe_allow_html=True)

        # ── Admin login / logout ───────────────────────────────────────────────
        st.markdown("---")
        if st.session_state["is_admin"]:
            st.markdown(
                '<p style="font-size:0.78rem;color:#5f6772;margin:0 0 4px 0;">🔐 Admin mode</p>',
                unsafe_allow_html=True,
            )
            if st.button("Logout", key="admin_logout", use_container_width=True):
                st.session_state["is_admin"] = False
                st.rerun()
        else:
            with st.expander("🔐 Admin Login", expanded=False):
                _pwd = st.text_input("Password", type="password", key="admin_pwd_input", label_visibility="collapsed")
                if st.button("Login", key="admin_login_btn", use_container_width=True):
                    if _pwd == _admin_password:
                        st.session_state["is_admin"] = True
                        st.rerun()
                    else:
                        st.error("Incorrect password")

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

    if page == "download":
        show_ncbi_download()
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

        st.caption("Leave the query blank to list all datasets that match the selected filters, such as Homo sapiens in a custom date range.")

    # Perform search
    has_filters = bool(organisms) or (tech_type and tech_type != "All") or bool(date_start) or bool(date_end)

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

                # Export annotation comparison to output/
                try:
                    out_path = export_result_descriptions(query, results["results"])
                    st.session_state["_export_path"] = str(out_path)
                    st.session_state["_export_content"] = out_path.read_text(encoding="utf-8")
                except Exception as export_err:
                    logger.warning(f"Annotation export failed: {export_err}")
                    st.session_state.pop("_export_path", None)
            except Exception as e:
                st.error(f"Search failed: {str(e)}")
                logger.error(f"Search error: {e}", exc_info=True)
                st.info(
                    "If Milvus is not running, semantic search will be disabled. "
                    "Make sure all services are running via docker-compose."
                )

    elif search_clicked and has_filters:
        with st.spinner("Loading filtered results..."):
            try:
                results = perform_filtered_search(
                    organisms=organisms,
                    tech_type=tech_type if tech_type != "All" else None,
                    date_start=datetime.combine(date_start, datetime.min.time()) if date_start else None,
                    date_end=datetime.combine(date_end, datetime.max.time()) if date_end else None,
                )
                st.session_state["_results"] = results
                st.session_state["_search_query"] = ""
                st.session_state["_use_mesh"] = False
                st.session_state.page = 1
            except Exception as e:
                st.error(f"Filtered search failed: {str(e)}")
                logger.error(f"Filtered search error: {e}", exc_info=True)

    elif search_clicked:
        st.warning("Please enter a search query or choose at least one filter.")

    # Show annotation export status + download button if available
    if st.session_state.get("_export_path") and st.session_state.get("_export_content"):
        _exp_path = st.session_state["_export_path"]
        _exp_content = st.session_state["_export_content"]
        _exp_fname = Path(_exp_path).name
        col_info, col_dl = st.columns([5, 2])
        with col_info:
            st.success(f"Annotations export saved to `output/{_exp_fname}`")
        with col_dl:
            st.download_button(
                label="⬇ Descriptions (.md)",
                data=_exp_content,
                file_name=_exp_fname,
                mime="text/markdown",
                use_container_width=True,
                key="dl_descriptions",
            )

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
