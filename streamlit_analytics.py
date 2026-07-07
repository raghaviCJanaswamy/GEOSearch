"""
Analytics dashboard for GEOSearch.
Tabs:
  1. DB Overview + Field Coverage
  2. Study Types
  3. Organisms
  4. MeSH Coverage & Quality
  5. Search Benchmark (Precision vs NCBI)
  6. Retrieval Pipeline Comparison
  7. Performance Latency
"""
import time
import streamlit as st
import pandas as pd
from sqlalchemy import text
from db.session import SessionLocal


# ── helpers ───────────────────────────────────────────────────────────────────

def _run(sql: str, params: dict | None = None) -> list[dict]:
    with SessionLocal() as db:
        result = db.execute(text(sql), params or {})
        cols = result.keys()
        return [dict(zip(cols, row)) for row in result.fetchall()]


def _bar(df: pd.DataFrame, x: str, y: str):
    st.bar_chart(df.set_index(x)[y])


# ── Section 1: DB Overview ────────────────────────────────────────────────────

def _section_db_overview():
    st.subheader("Database Overview")
    rows = _run("""
        SELECT
            COUNT(*)                                        AS total_series,
            COUNT(tech_type)                                AS has_tech_type,
            COUNT(sample_count)                             AS has_sample_count,
            COUNT(submission_date)                          AS has_submission_date,
            COUNT(NULLIF(overall_design, ''))               AS has_overall_design,
            COUNT(NULLIF(summary, ''))                      AS has_summary,
            COUNT(NULLIF(organism_text, ''))                AS has_organism
        FROM gse_series
    """)
    r = rows[0]
    total = r["total_series"]

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Total GSE Series",     f"{total:,}")
    c2.metric("Has Summary",          f"{r['has_summary']:,}",      f"{r['has_summary']/total*100:.1f}%" if total else "0%")
    c3.metric("Has Overall Design",   f"{r['has_overall_design']:,}", f"{r['has_overall_design']/total*100:.1f}%" if total else "0%")
    c4.metric("Has Tech Type",        f"{r['has_tech_type']:,}",    f"{r['has_tech_type']/total*100:.1f}%" if total else "0%")
    c5.metric("Has Sample Count",     f"{r['has_sample_count']:,}", f"{r['has_sample_count']/total*100:.1f}%" if total else "0%")
    c6.metric("Has Organism",         f"{r['has_organism']:,}",     f"{r['has_organism']/total*100:.1f}%" if total else "0%")

    col1, col2 = st.columns(2)
    with col1:
        mesh_rows = _run("SELECT COUNT(*) AS cnt FROM mesh_term")
        st.metric("MeSH Terms Loaded", f"{mesh_rows[0]['cnt']:,}")
    with col2:
        vec_rows = _run("SELECT COUNT(*) AS cnt FROM gse_series WHERE accession IS NOT NULL")
        st.metric("Records in DB", f"{vec_rows[0]['cnt']:,}")


def _section_field_coverage():
    st.subheader("Metadata Field Coverage")
    rows = _run("""
        SELECT
            COUNT(*) AS total,
            COUNT(NULLIF(title, ''))          AS title,
            COUNT(NULLIF(summary, ''))        AS summary,
            COUNT(NULLIF(overall_design, '')) AS overall_design,
            COUNT(NULLIF(organism_text, ''))  AS organism,
            COUNT(tech_type)                  AS tech_type,
            COUNT(sample_count)               AS sample_count,
            COUNT(submission_date)            AS submission_date,
            COUNT(pubmed_ids)                 AS pubmed_ids
        FROM gse_series
    """)
    r = rows[0]
    total = r["total"]
    if total == 0:
        st.info("No data in database.")
        return

    fields = ["title", "summary", "overall_design", "organism",
              "tech_type", "sample_count", "submission_date", "pubmed_ids"]
    df = pd.DataFrame({
        "Field":       fields,
        "Populated":   [r[f] for f in fields],
        "Missing":     [total - r[f] for f in fields],
        "Coverage %":  [round(r[f] / total * 100, 1) for f in fields],
    })
    st.dataframe(df, use_container_width=True, hide_index=True)
    _bar(df, "Field", "Coverage %")

    # Highlight missing overall_design
    od_pct = r["overall_design"] / total * 100 if total else 0
    if od_pct < 10:
        st.warning(
            f"⚠️ Only {od_pct:.1f}% of records have `overall_design` populated. "
            "This reduces recall for queries that match study design descriptions. "
            "Run the NCBI backfill script to enrich this field."
        )


# ── Section 2: Study Types ────────────────────────────────────────────────────

def _section_tech_type():
    st.subheader("Study Type Distribution")
    rows = _run("""
        SELECT COALESCE(tech_type, 'unknown') AS tech_type, COUNT(*) AS cnt
        FROM gse_series
        GROUP BY tech_type
        ORDER BY cnt DESC
    """)
    if not rows:
        st.info("No data yet.")
        return

    df = pd.DataFrame(rows).rename(columns={"tech_type": "Study Type", "cnt": "Count"})
    total = df["Count"].sum()
    df["% of Total"] = (df["Count"] / total * 100).map(lambda x: f"{x:.1f}%")
    _bar(df, "Study Type", "Count")
    st.dataframe(df, use_container_width=True, hide_index=True)


# ── Section 3: Organisms ──────────────────────────────────────────────────────

def _section_organism():
    st.subheader("Top Organisms")
    rows = _run("""
        SELECT organism_text AS organism, COUNT(*) AS cnt
        FROM gse_series
        WHERE organism_text IS NOT NULL AND organism_text != ''
        GROUP BY organism_text
        ORDER BY cnt DESC
        LIMIT 20
    """)
    if not rows:
        st.info("No organism data available.")
        return

    df = pd.DataFrame(rows).rename(columns={"organism": "Organism", "cnt": "Count"})
    total = df["Count"].sum()
    df["% of Total"] = (df["Count"] / total * 100).map(lambda x: f"{x:.1f}%")
    _bar(df, "Organism", "Count")
    st.dataframe(df, use_container_width=True, hide_index=True)


# ── Section 4: MeSH Coverage & Quality ───────────────────────────────────────

def _section_mesh_coverage():
    st.subheader("MeSH Tag Coverage")
    rows = _run("""
        SELECT
            COUNT(DISTINCT g.accession)   AS total_series,
            COUNT(DISTINCT gm.accession)  AS tagged_series,
            COUNT(gm.mesh_id)             AS total_tags
        FROM gse_series g
        LEFT JOIN gse_mesh gm ON g.accession = gm.accession
    """)
    r = rows[0]
    total  = r["total_series"]
    tagged = r["tagged_series"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Total Series",       f"{total:,}")
    c2.metric("MeSH-Tagged Series", f"{tagged:,}", f"{tagged/total*100:.1f}%" if total else "0%")
    c3.metric("Total MeSH Tags",    f"{r['total_tags']:,}")

    if tagged == 0:
        st.warning("No MeSH tags yet. Run the MeSH auto-tagger after ingesting data.")
        return

    st.subheader("Top MeSH Terms Across Corpus")
    top_mesh = _run("""
        SELECT mt.preferred_name AS term, COUNT(gm.accession) AS series_count
        FROM gse_mesh gm
        JOIN mesh_term mt ON gm.mesh_id = mt.mesh_id
        GROUP BY mt.preferred_name
        ORDER BY series_count DESC
        LIMIT 20
    """)
    if top_mesh:
        df = pd.DataFrame(top_mesh).rename(columns={"term": "MeSH Term", "series_count": "Series Count"})
        _bar(df, "MeSH Term", "Series Count")
        st.dataframe(df, use_container_width=True, hide_index=True)

    st.subheader("MeSH Expansion Quality — Known Queries")
    st.caption("Tests whether MeSH lookup correctly maps lay terms to clinical descriptors.")
    EXPANSION_TESTS = [
        {"query": "heart attack",       "expected": "Myocardial Infarction"},
        {"query": "breast cancer",      "expected": "Breast Neoplasms"},
        {"query": "pancreatic cancer",  "expected": "Pancreatic Neoplasms"},
        {"query": "Alzheimer's disease","expected": "Alzheimer Disease"},
        {"query": "COVID-19",           "expected": "COVID-19"},
        {"query": "diabetes",           "expected": "Diabetes Mellitus"},
    ]
    if st.button("Run MeSH Expansion Tests"):
        from mesh.query_expand import QueryExpander
        results = []
        for t in EXPANSION_TESTS:
            try:
                with SessionLocal() as db:
                    expander = QueryExpander(db)
                    res = expander.expand_query(t["query"])
                terms_found = [m["preferred_name"] for m in res.get("matched_terms", [])]
                hit = t["expected"] in terms_found
                results.append({
                    "Query":          t["query"],
                    "Expected Term":  t["expected"],
                    "Found":          "✅" if hit else "❌",
                    "All Terms Found": ", ".join(terms_found[:5]),
                })
            except Exception as e:
                results.append({
                    "Query": t["query"], "Expected Term": t["expected"],
                    "Found": "❌", "All Terms Found": str(e),
                })
        df = pd.DataFrame(results)
        hits = sum(1 for r in results if r["Found"] == "✅")
        st.metric("Expansion Accuracy", f"{hits}/{len(results)}", f"{hits/len(results)*100:.0f}%")
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("Click **Run MeSH Expansion Tests** to validate MeSH mapping quality.")

    st.subheader("Blocklist Effectiveness")
    st.caption("Overly-broad MeSH IDs are blocked to prevent over-retrieval.")
    BLOCKLIST = {
        "D009369": "Neoplasms",
        "D006801": "Humans",
        "D000818": "Animals",
        "D051379": "Mice",
        "D005260": "Female",
        "D008297": "Male",
        "D000368": "Aged",
        "D008875": "Middle Aged",
        "D000328": "Adult",
        "D006243": "Healthy Volunteers",
        "D015493": "CD4-Positive T-Lymphocytes",
        "D013997": "Time Factors",
    }
    bl_rows = []
    for mesh_id, name in BLOCKLIST.items():
        cnt = _run("SELECT COUNT(*) AS cnt FROM gse_mesh WHERE mesh_id = :mid", {"mid": mesh_id})
        bl_rows.append({"MeSH ID": mesh_id, "Term": name, "Tagged Series (would match)": cnt[0]["cnt"]})
    df_bl = pd.DataFrame(bl_rows)
    st.dataframe(df_bl, use_container_width=True, hide_index=True)
    st.caption("These terms are blocked from query expansion — matching them would cause over-retrieval.")


# ── Section 5: Search Benchmark ───────────────────────────────────────────────

DEFAULT_BENCHMARK_QUERIES = [
    "pancreatic cancer",
    "breast cancer",
    "heart attack",
    "Alzheimer's disease",
    "COVID-19",
    "lung cancer",
    "diabetes",
]

@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_ncbi_count(query: str) -> int | None:
    """Fetch live result count from NCBI GEO via E-utilities esearch API."""
    import requests
    term = f'({query}) AND "Homo sapiens"[porgn:__txid9606] AND GSE[ETYP]'
    try:
        resp = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            params={"db": "gds", "term": term, "rettype": "count", "retmode": "json"},
            timeout=10,
        )
        resp.raise_for_status()
        return int(resp.json()["esearchresult"]["count"])
    except Exception:
        return None


def _section_search_benchmark():
    st.subheader("Search Result Benchmarking")
    st.caption(
        "Compares GEOSearch result counts against **live NCBI GEO counts** fetched via "
        "E-utilities esearch API (`term=(query) AND \"Homo sapiens\"[porgn:__txid9606] AND GSE[ETYP]`)."
    )

    # Let user add/remove queries
    with st.expander("⚙️ Customize benchmark queries"):
        custom = st.text_area(
            "One query per line",
            value="\n".join(DEFAULT_BENCHMARK_QUERIES),
            height=160,
            key="bench_queries",
        )
        queries = [q.strip() for q in custom.strip().splitlines() if q.strip()]

    col1, col2 = st.columns([1, 3])
    run_clicked   = col1.button("▶ Run Benchmark", key="run_bench")
    ncbi_live     = col2.checkbox("Fetch live NCBI counts (requires internet)", value=True, key="ncbi_live")

    if run_clicked:
        from search import HybridSearchEngine
        results  = []
        progress = st.progress(0)
        status   = st.empty()
        total_q  = len(queries)

        for i, query in enumerate(queries):
            # ── NCBI live count ──
            status.text(f"[{i+1}/{total_q}] Fetching NCBI count for: {query} …")
            if ncbi_live:
                ncbi_count = _fetch_ncbi_count(query)
            else:
                ncbi_count = None

            # ── GEOSearch count ──
            status.text(f"[{i+1}/{total_q}] Running GEOSearch for: {query} …")
            try:
                with SessionLocal() as db:
                    engine  = HybridSearchEngine(db)
                    t0      = time.time()
                    res     = engine.search(query=query, top_k=10000)
                    elapsed = round((time.time() - t0) * 1000)
                geo_count = res.get("total", len(res.get("results", [])))
            except Exception as e:
                geo_count = None
                elapsed   = None

            # ── compute gap & coverage ──
            if isinstance(geo_count, int) and isinstance(ncbi_count, int) and ncbi_count > 0:
                gap = geo_count - ncbi_count
                cov = round(geo_count / ncbi_count * 100, 1)
            else:
                gap = None
                cov = None

            results.append({
                "Query":        query,
                "NCBI GEO":     ncbi_count if ncbi_count is not None else "N/A (offline)",
                "GEOSearch":    geo_count  if geo_count  is not None else "Error",
                "Gap":          gap        if gap         is not None else "N/A",
                "Coverage %":   f"{cov}%"  if cov         is not None else "N/A",
                "Latency (ms)": elapsed    if elapsed     is not None else "N/A",
            })
            progress.progress((i + 1) / total_q)

        status.empty()
        progress.empty()

        df = pd.DataFrame(results)
        st.dataframe(df, use_container_width=True, hide_index=True)

        # ── summary metrics ──
        covered = [r for r in results if isinstance(r["GEOSearch"], int) and isinstance(r["NCBI GEO"], int)]
        if covered:
            avg_cov = sum(r["GEOSearch"] / r["NCBI GEO"] * 100 for r in covered) / len(covered)
            over    = sum(1 for r in covered if isinstance(r["Gap"], int) and r["Gap"] > 0)
            under   = sum(1 for r in covered if isinstance(r["Gap"], int) and r["Gap"] < 0)
            c1, c2, c3 = st.columns(3)
            c1.metric("Avg Coverage vs NCBI",     f"{avg_cov:.1f}%")
            c2.metric("Queries Over-Retrieving",  str(over))
            c3.metric("Queries Under-Retrieving", str(under))

            # ── side-by-side bar chart ──
            chart_df = pd.DataFrame({
                "Query":     [r["Query"] for r in covered],
                "NCBI GEO":  [r["NCBI GEO"] for r in covered],
                "GEOSearch": [r["GEOSearch"] for r in covered],
            }).set_index("Query")
            st.bar_chart(chart_df)

        if not ncbi_live:
            st.info("ℹ️ NCBI counts not fetched — enable **Fetch live NCBI counts** checkbox to compare against live data.")
    else:
        st.info("Customize queries above, then click **▶ Run Benchmark**. Each query fetches a live NCBI count and runs GEOSearch.")


# ── Section 6: Retrieval Pipeline Comparison ──────────────────────────────────

def _section_pipeline_comparison():
    st.subheader("Retrieval Pipeline Comparison")
    st.caption("Compare result counts from Semantic-only vs Lexical-only vs Full Hybrid vs MeSH ON/OFF.")

    query = st.text_input("Query to compare", value="pancreatic cancer", key="pipe_query")

    if st.button("▶ Run Pipeline Comparison", key="run_pipe"):
        from search import HybridSearchEngine
        configs = [
            {"label": "Semantic only",          "semantic": True,  "lexical": False, "mesh": False},
            {"label": "Lexical only",            "semantic": False, "lexical": True,  "mesh": False},
            {"label": "Hybrid (no MeSH)",        "semantic": True,  "lexical": True,  "mesh": False},
            {"label": "Hybrid + MeSH",           "semantic": True,  "lexical": True,  "mesh": True},
        ]
        results = []
        progress = st.progress(0)
        for i, cfg in enumerate(configs):
            try:
                with SessionLocal() as db:
                    engine = HybridSearchEngine(db)
                    t0 = time.time()
                    res = engine.search(
                        query=query,
                        top_k=5000,
                        use_semantic=cfg["semantic"],
                        use_lexical=cfg["lexical"],
                        use_mesh=cfg["mesh"],
                    )
                    elapsed = round((time.time() - t0) * 1000)
                count = res.get("total", len(res.get("results", [])))
                mesh_terms = res.get("mesh_terms", [])
            except Exception as e:
                count, elapsed, mesh_terms = f"Error: {e}", None, []
            results.append({
                "Config":       cfg["label"],
                "Results":      count,
                "Latency (ms)": elapsed,
                "MeSH Terms":   len(mesh_terms),
            })
            progress.progress((i + 1) / len(configs))

        df = pd.DataFrame(results)
        st.dataframe(df, use_container_width=True, hide_index=True)

        valid = [r for r in results if isinstance(r["Results"], int)]
        if valid:
            chart_df = pd.DataFrame({"Config": [r["Config"] for r in valid],
                                     "Results": [r["Results"] for r in valid]}).set_index("Config")
            st.bar_chart(chart_df)

        # MeSH expansion detail
        st.subheader("MeSH Expansion Detail")
        try:
            from mesh.query_expand import QueryExpander
            with SessionLocal() as db:
                expander = QueryExpander(db)
                expansion = expander.expand_query(query)
            terms = expansion.get("matched_terms", [])
            if terms:
                df_terms = pd.DataFrame(terms)[["preferred_name", "mesh_id"]].rename(
                    columns={"preferred_name": "MeSH Term", "mesh_id": "MeSH ID"})
                st.dataframe(df_terms, use_container_width=True, hide_index=True)
            else:
                st.info("No MeSH terms found for this query.")
        except Exception as e:
            st.warning(f"MeSH expansion failed: {e}")
    else:
        st.info("Enter a query and click **▶ Run Pipeline Comparison**.")


# ── Section 7: Performance Latency ────────────────────────────────────────────

def _section_performance():
    st.subheader("Query Latency Profiling")
    st.caption("Measures time spent in each pipeline stage: MeSH expansion, semantic search, lexical search, RRF fusion.")

    query = st.text_input("Query to profile", value="breast cancer RNA-seq", key="perf_query")
    runs  = st.slider("Number of runs (for avg)", min_value=1, max_value=5, value=3, key="perf_runs")

    if st.button("▶ Profile Query", key="run_perf"):
        from search import HybridSearchEngine
        from mesh.query_expand import QueryExpander

        all_latencies = []
        progress = st.progress(0)

        for i in range(runs):
            latencies = {}

            # MeSH expansion
            t0 = time.time()
            try:
                with SessionLocal() as db:
                    expander = QueryExpander(db)
                    expansion = expander.expand_query(query)
                mesh_terms = expansion.get("matched_terms", [])
            except Exception:
                mesh_terms = []
            latencies["MeSH Expansion"] = round((time.time() - t0) * 1000)

            # Full hybrid search
            t0 = time.time()
            try:
                with SessionLocal() as db:
                    engine = HybridSearchEngine(db)
                    res = engine.search(query=query, top_k=100)
                total_results = res.get("total", 0)
            except Exception as e:
                total_results = 0
            latencies["Full Hybrid Search"] = round((time.time() - t0) * 1000)

            # Semantic-only
            t0 = time.time()
            try:
                with SessionLocal() as db:
                    engine = HybridSearchEngine(db)
                    engine.search(query=query, top_k=100, use_semantic=True, use_lexical=False, use_mesh=False)
            except Exception:
                pass
            latencies["Semantic Only"] = round((time.time() - t0) * 1000)

            # Lexical-only
            t0 = time.time()
            try:
                with SessionLocal() as db:
                    engine = HybridSearchEngine(db)
                    engine.search(query=query, top_k=100, use_semantic=False, use_lexical=True, use_mesh=False)
            except Exception:
                pass
            latencies["Lexical Only"] = round((time.time() - t0) * 1000)

            all_latencies.append(latencies)
            progress.progress((i + 1) / runs)

        # Average across runs
        avg = {k: round(sum(r[k] for r in all_latencies) / runs) for k in all_latencies[0]}
        df  = pd.DataFrame([
            {"Stage": k, "Avg Latency (ms)": v, "P95 (ms)": max(r[k] for r in all_latencies)}
            for k, v in avg.items()
        ])
        st.metric("Total Results", total_results)
        st.metric("MeSH Terms Expanded", len(mesh_terms))
        st.dataframe(df, use_container_width=True, hide_index=True)
        _bar(df, "Stage", "Avg Latency (ms)")

        st.caption(f"Averaged over {runs} run(s). Full hybrid includes MeSH expansion + semantic + lexical + RRF.")
    else:
        st.info("Enter a query and click **▶ Profile Query**.")


# ── main entry point ───────────────────────────────────────────────────────────

def show_analytics_dashboard():
    st.title("Analytics Dashboard")
    st.caption("Database statistics, metadata coverage, MeSH tagging, search benchmarking, and performance profiling.")

    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
        "📊 Overview",
        "🔬 Study Types",
        "🧬 Organisms",
        "🏷️ MeSH Coverage",
        "🎯 Search Benchmark",
        "⚡ Pipeline Comparison",
        "⏱️ Performance",
    ])

    with tab1:
        _section_db_overview()
        st.markdown("---")
        _section_field_coverage()

    with tab2:
        _section_tech_type()

    with tab3:
        _section_organism()

    with tab4:
        _section_mesh_coverage()

    with tab5:
        _section_search_benchmark()

    with tab6:
        _section_pipeline_comparison()

    with tab7:
        _section_performance()
