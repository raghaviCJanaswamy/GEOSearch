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
from collections import Counter

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
                    res     = engine.search(query=query)
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
                    res = engine.search(query=query)
                total_results = res.get("total", 0)
            except Exception as e:
                total_results = 0
            latencies["Full Hybrid Search"] = round((time.time() - t0) * 1000)

            # Semantic-only
            t0 = time.time()
            try:
                with SessionLocal() as db:
                    engine = HybridSearchEngine(db)
                    engine.search(query=query, use_semantic=True, use_lexical=False, use_mesh=False)
            except Exception:
                pass
            latencies["Semantic Only"] = round((time.time() - t0) * 1000)

            # Lexical-only
            t0 = time.time()
            try:
                with SessionLocal() as db:
                    engine = HybridSearchEngine(db)
                    engine.search(query=query, use_semantic=False, use_lexical=True, use_mesh=False)
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


# ── Section 8: IR Evaluation — P@10 and MRR ───────────────────────────────────

# 50 curated queries with relevance signals drawn from MeSH tags.
# Each entry: query text + list of expected MeSH preferred names that a
# relevant result should be tagged with (at least one must match for a result
# to count as relevant). This lets us compute relevance without manual
# annotation: a dataset tagged with the correct disease MeSH term is
# considered relevant.
IR_EVAL_QUERIES: list[dict] = [
    # Oncology (10)
    {"query": "breast cancer RNA-seq",           "relevant_mesh": ["Breast Neoplasms"]},
    {"query": "lung cancer gene expression",     "relevant_mesh": ["Lung Neoplasms"]},
    {"query": "pancreatic cancer transcriptome", "relevant_mesh": ["Pancreatic Neoplasms"]},
    {"query": "colorectal cancer methylation",   "relevant_mesh": ["Colorectal Neoplasms"]},
    {"query": "prostate cancer single cell",     "relevant_mesh": ["Prostatic Neoplasms"]},
    {"query": "ovarian cancer RNA sequencing",   "relevant_mesh": ["Ovarian Neoplasms"]},
    {"query": "liver cancer hepatocellular",     "relevant_mesh": ["Carcinoma, Hepatocellular"]},
    {"query": "glioblastoma brain tumor",        "relevant_mesh": ["Glioblastoma"]},
    {"query": "leukemia ALL childhood",          "relevant_mesh": ["Precursor Cell Lymphoblastic Leukemia-Lymphoma"]},
    {"query": "melanoma immunotherapy",          "relevant_mesh": ["Melanoma"]},
    # Neurological (8)
    {"query": "Alzheimer's disease brain",       "relevant_mesh": ["Alzheimer Disease"]},
    {"query": "Parkinson's disease dopamine",    "relevant_mesh": ["Parkinson Disease"]},
    {"query": "multiple sclerosis neuroinflammation", "relevant_mesh": ["Multiple Sclerosis"]},
    {"query": "epilepsy seizure gene",           "relevant_mesh": ["Epilepsy"]},
    {"query": "autism spectrum disorder",        "relevant_mesh": ["Autistic Disorder"]},
    {"query": "schizophrenia prefrontal cortex", "relevant_mesh": ["Schizophrenia"]},
    {"query": "stroke ischemia brain",           "relevant_mesh": ["Stroke"]},
    {"query": "ALS motor neuron disease",        "relevant_mesh": ["Amyotrophic Lateral Sclerosis"]},
    # Metabolic / Cardiovascular (8)
    {"query": "type 2 diabetes insulin resistance", "relevant_mesh": ["Diabetes Mellitus, Type 2"]},
    {"query": "obesity adipose tissue",          "relevant_mesh": ["Obesity"]},
    {"query": "heart failure cardiac",           "relevant_mesh": ["Heart Failure"]},
    {"query": "atherosclerosis coronary artery", "relevant_mesh": ["Atherosclerosis"]},
    {"query": "myocardial infarction heart attack", "relevant_mesh": ["Myocardial Infarction"]},
    {"query": "hypertension blood pressure",     "relevant_mesh": ["Hypertension"]},
    {"query": "non-alcoholic fatty liver NASH",  "relevant_mesh": ["Non-alcoholic Fatty Liver Disease"]},
    {"query": "kidney disease renal fibrosis",   "relevant_mesh": ["Renal Insufficiency, Chronic"]},
    # Infectious / Immune (8)
    {"query": "COVID-19 SARS-CoV-2",            "relevant_mesh": ["COVID-19"]},
    {"query": "HIV AIDS immune",                 "relevant_mesh": ["HIV Infections"]},
    {"query": "tuberculosis mycobacterium",      "relevant_mesh": ["Tuberculosis"]},
    {"query": "influenza virus infection",       "relevant_mesh": ["Influenza, Human"]},
    {"query": "sepsis inflammatory response",    "relevant_mesh": ["Sepsis"]},
    {"query": "lupus autoimmune SLE",            "relevant_mesh": ["Lupus Erythematosus, Systemic"]},
    {"query": "rheumatoid arthritis joint",      "relevant_mesh": ["Arthritis, Rheumatoid"]},
    {"query": "malaria plasmodium",              "relevant_mesh": ["Malaria"]},
    # Rare / Niche (8)
    {"query": "vitiligo melanocyte",             "relevant_mesh": ["Vitiligo"]},
    {"query": "cystic fibrosis CFTR",            "relevant_mesh": ["Cystic Fibrosis"]},
    {"query": "Huntington's disease neurodegeneration", "relevant_mesh": ["Huntington Disease"]},
    {"query": "sickle cell disease hemoglobin", "relevant_mesh": ["Anemia, Sickle Cell"]},
    {"query": "muscular dystrophy DMD",          "relevant_mesh": ["Muscular Dystrophies"]},
    {"query": "Niemann-Pick sphingolipid",       "relevant_mesh": ["Niemann-Pick Diseases"]},
    {"query": "Gaucher disease glucocerebrosidase", "relevant_mesh": ["Gaucher Disease"]},
    {"query": "phenylketonuria PKU metabolism",  "relevant_mesh": ["Phenylketonurias"]},
    # Technology / Cross-cutting (8)
    {"query": "single-cell RNA-seq scRNA",       "relevant_mesh": ["Single-Cell Analysis"]},
    {"query": "ATAC-seq chromatin accessibility","relevant_mesh": ["Chromatin"]},
    {"query": "ChIP-seq histone modification",   "relevant_mesh": ["Histones"]},
    {"query": "CRISPR gene editing",             "relevant_mesh": ["CRISPR-Cas Systems"]},
    {"query": "spatial transcriptomics tissue",  "relevant_mesh": ["Transcriptome"]},
    {"query": "proteomics mass spectrometry",    "relevant_mesh": ["Proteomics"]},
    {"query": "microbiome gut 16S",              "relevant_mesh": ["Microbiota"]},
    {"query": "DNA methylation epigenome WGBS",  "relevant_mesh": ["DNA Methylation"]},
]


def _evaluate_query_ir(
    query: str,
    relevant_mesh: list[str],
    engine,
    db_session,
    k: int = 10,
    use_semantic: bool = True,
    use_lexical: bool = True,
    use_mesh: bool = True,
) -> dict:
    """Run a single query, return P@k and first-relevant rank."""
    from sqlalchemy import text as _text

    t0 = time.time()
    res = engine.search(
        query=query,
        use_semantic=use_semantic,
        use_lexical=use_lexical,
        use_mesh=use_mesh,
    )
    elapsed = round((time.time() - t0) * 1000)

    results = res.get("results", [])[:k]
    if not results:
        return {"P@10": 0.0, "RR": 0.0, "latency_ms": elapsed, "n_results": 0}

    # For each result, check whether it has at least one relevant MeSH tag
    accessions = [r["accession"] for r in results]
    relevant_set = set(m.lower() for m in relevant_mesh)

    # Batch query: which accessions have a relevant MeSH tag?
    placeholders = ", ".join(f":acc_{i}" for i in range(len(accessions)))
    params = {f"acc_{i}": acc for i, acc in enumerate(accessions)}
    sql = f"""
        SELECT DISTINCT gm.accession
        FROM gse_mesh gm
        JOIN mesh_term mt ON gm.mesh_id = mt.mesh_id
        WHERE gm.accession IN ({placeholders})
          AND LOWER(mt.preferred_name) = ANY(:mesh_names)
    """
    params["mesh_names"] = list(relevant_set)
    tagged = set(
        row[0] for row in db_session.execute(_text(sql), params).fetchall()
    )

    # Compute P@k and RR
    hits = [1 if acc in tagged else 0 for acc in accessions]
    p_at_k = sum(hits) / k
    rr = 0.0
    for rank, h in enumerate(hits, start=1):
        if h == 1:
            rr = 1.0 / rank
            break

    return {"P@10": round(p_at_k, 3), "RR": round(rr, 3), "latency_ms": elapsed, "n_results": len(results)}


def _section_ir_evaluation():
    st.subheader("IR Evaluation — P@10 and MRR")
    st.caption(
        "Evaluates retrieval quality using MeSH-tag relevance as a proxy for relevance labels. "
        "A result is considered **relevant** if it is tagged with at least one of the expected MeSH terms "
        "for that query (e.g., a 'breast cancer' query is relevant if the dataset has 'Breast Neoplasms' tag). "
        "Computes **P@10** (fraction of top-10 results that are relevant) and "
        "**MRR** (Mean Reciprocal Rank — position of first relevant result)."
    )

    with st.expander("Query Set (50 queries across 5 domains)", expanded=False):
        df_queries = pd.DataFrame([
            {"Query": q["query"], "Expected MeSH": ", ".join(q["relevant_mesh"])}
            for q in IR_EVAL_QUERIES
        ])
        st.dataframe(df_queries, use_container_width=True, hide_index=True)

    col1, col2, col3 = st.columns([1, 1, 2])
    run_eval = col1.button("▶ Run Full Evaluation (50 queries)", key="run_ir_eval")
    run_sample = col2.button("▶ Quick Sample (10 queries)", key="run_ir_sample")
    k_val = col3.slider("k (Precision@k)", min_value=5, max_value=20, value=10, step=5, key="ir_k")

    queries_to_run = None
    if run_eval:
        queries_to_run = IR_EVAL_QUERIES
    elif run_sample:
        # Pick 2 from each domain block
        queries_to_run = IR_EVAL_QUERIES[::5]  # every 5th → 10 queries spread across domains

    if queries_to_run:
        from search import HybridSearchEngine

        results = []
        progress = st.progress(0)
        status = st.empty()
        n = len(queries_to_run)

        for i, q_entry in enumerate(queries_to_run):
            status.text(f"[{i+1}/{n}] {q_entry['query']}")
            try:
                with SessionLocal() as db:
                    engine = HybridSearchEngine(db)
                    metrics = _evaluate_query_ir(
                        q_entry["query"], q_entry["relevant_mesh"], engine, db, k=k_val
                    )
                results.append({
                    "Query": q_entry["query"],
                    "Expected MeSH": q_entry["relevant_mesh"][0],
                    f"P@{k_val}": metrics["P@10"],
                    "RR": metrics["RR"],
                    "Latency (ms)": metrics["latency_ms"],
                    "Results": metrics["n_results"],
                })
            except Exception as e:
                st.error(f"Query '{q_entry['query']}' failed: {type(e).__name__}: {e}")
                results.append({
                    "Query": q_entry["query"],
                    "Expected MeSH": q_entry["relevant_mesh"][0],
                    f"P@{k_val}": "Error",
                    "RR": "Error",
                    "Latency (ms)": None,
                    "Results": 0,
                })
            progress.progress((i + 1) / n)

        status.empty()
        progress.empty()

        # Summary metrics
        valid = [r for r in results if isinstance(r[f"P@{k_val}"], float)]
        if valid:
            mean_p = sum(r[f"P@{k_val}"] for r in valid) / len(valid)
            mean_mrr = sum(r["RR"] for r in valid) / len(valid)
            avg_lat = sum(r["Latency (ms)"] for r in valid if r["Latency (ms)"]) / len(valid)

            mc1, mc2, mc3, mc4 = st.columns(4)
            mc1.metric(f"Mean P@{k_val}", f"{mean_p:.3f}")
            mc2.metric("MRR", f"{mean_mrr:.3f}")
            mc3.metric("Queries Evaluated", len(valid))
            mc4.metric("Avg Latency (ms)", f"{avg_lat:.0f}")

            st.info(
                f"**GEOSearch (this run):** P@{k_val} = {mean_p:.3f} · MRR = {mean_mrr:.3f}"
            )

        df_res = pd.DataFrame(results)
        st.dataframe(df_res, use_container_width=True, hide_index=True)

        # P@k bar chart
        if valid:
            chart_df = pd.DataFrame({
                "Query": [r["Query"][:40] for r in valid],
                f"P@{k_val}": [r[f"P@{k_val}"] for r in valid],
                "RR": [r["RR"] for r in valid],
            }).set_index("Query")
            st.bar_chart(chart_df)
    else:
        st.info(
            "Click **▶ Run Full Evaluation** to score all 50 queries, "
            "or **▶ Quick Sample** for a 10-query preview."
        )


# ── Section 9: K-Means Clustering + PCA ───────────────────────────────────────

def _section_kmeans_clustering():
    st.subheader("K-Means Topic Clustering + PCA Visualization")
    st.caption(
        "Fetches dataset embeddings from Milvus, runs K-Means clustering (k = user-selected), "
        "reduces to 2D via PCA, and labels each cluster with top TF-IDF n-grams from member titles. "
        "Clusters GEO datasets by embedding similarity on the Human Cancer subset."
    )

    # Controls
    c1, c2, c3, c4 = st.columns(4)
    k_clusters = c1.slider("Number of clusters (k)", 3, 20, 8, key="km_k")
    subset_org  = c2.selectbox("Organism filter", ["Homo sapiens", "Mus musculus", "All"], key="km_org")
    subset_tech = c3.selectbox("Tech type filter", ["All", "Expression profiling by high throughput sequencing",
                                                     "Expression profiling by array",
                                                     "Methylation profiling by high throughput sequencing",
                                                     "Single-cell RNA sequencing"], key="km_tech")
    max_records = c4.number_input("Max records to cluster", 500, 20000, 5000, 500, key="km_max")

    run_cluster = st.button("▶ Run K-Means Clustering", key="run_kmeans", type="primary")

    if not run_cluster:
        st.info(
            "Select filters and k, then click **▶ Run K-Means Clustering**. "
            "Embeddings are fetched from Milvus — this may take 10–30 seconds for large subsets."
        )
        return

    # ── 1. Fetch accessions + metadata from PostgreSQL ─────────────────────────
    status = st.empty()
    status.text("Fetching dataset metadata from PostgreSQL...")

    org_filter = "" if subset_org == "All" else f"AND organism_text ILIKE '%{subset_org}%'"
    tech_filter = "" if subset_tech == "All" else f"AND tech_type = :tech_type"
    sql = f"""
        SELECT accession, title, tech_type, organism_text
        FROM gse_series
        WHERE accession IS NOT NULL
          AND title IS NOT NULL
          {org_filter}
          {tech_filter}
        ORDER BY submission_date DESC NULLS LAST
        LIMIT :lim
    """
    params: dict = {"lim": int(max_records)}
    if subset_tech != "All":
        params["tech_type"] = subset_tech

    rows = _run(sql, params)
    if not rows:
        st.warning("No records match the selected filters.")
        return

    accessions = [r["accession"] for r in rows]
    titles     = [r["title"] or "" for r in rows]
    tech_types = [r["tech_type"] or "unknown" for r in rows]
    status.text(f"Fetched {len(accessions)} records. Loading embeddings from Milvus...")

    # ── 2. Fetch embeddings from Milvus ────────────────────────────────────────
    try:
        from vector.milvus_store import MilvusStore
        import numpy as np
        store = MilvusStore()

        # Milvus query by accession — batch in chunks of 1000
        embeddings_map: dict[str, list[float]] = {}
        chunk_size = 1000
        for i in range(0, len(accessions), chunk_size):
            chunk = accessions[i:i + chunk_size]
            quoted = ", ".join(f'"{a}"' for a in chunk)
            hits = store.collection.query(
                expr=f"accession in [{quoted}]",
                output_fields=["accession", "embedding"],
                limit=chunk_size,
            )
            for h in hits:
                embeddings_map[h["accession"]] = h["embedding"]
            status.text(f"Loaded {len(embeddings_map)}/{len(accessions)} embeddings...")

        # Filter to records that have embeddings
        paired = [(a, t, tt, embeddings_map[a]) for a, t, tt in zip(accessions, titles, tech_types) if a in embeddings_map]
        if len(paired) < k_clusters:
            st.warning(f"Only {len(paired)} records have embeddings — need at least k={k_clusters}.")
            return

        acc_list   = [p[0] for p in paired]
        title_list = [p[1] for p in paired]
        tech_list  = [p[2] for p in paired]
        X = np.array([p[3] for p in paired], dtype=np.float32)

    except Exception as e:
        st.error(f"Could not load embeddings from Milvus: {e}")
        st.info("Ensure Milvus is running and embeddings have been generated (run backfill or ingest).")
        return

    status.text(f"Running K-Means (k={k_clusters}) on {X.shape[0]} × {X.shape[1]} embeddings...")

    # ── 3. K-Means ─────────────────────────────────────────────────────────────
    from sklearn.cluster import KMeans
    from sklearn.preprocessing import normalize
    from sklearn.decomposition import PCA
    from sklearn.feature_extraction.text import TfidfVectorizer
    import numpy as np

    X_norm = normalize(X, norm="l2")  # cosine ≈ euclidean after L2 norm
    km = KMeans(n_clusters=k_clusters, init="k-means++", n_init=10, random_state=42)
    labels = km.fit_predict(X_norm)

    # ── 4. TF-IDF cluster labels ───────────────────────────────────────────────
    status.text("Generating TF-IDF cluster labels...")
    tfidf = TfidfVectorizer(
        ngram_range=(1, 2),
        stop_words="english",
        max_features=10000,
        min_df=2,
    )
    tfidf.fit(title_list)
    feature_names = tfidf.get_feature_names_out()

    cluster_labels: dict[int, str] = {}
    cluster_sizes: dict[int, int] = {}
    for c in range(k_clusters):
        member_titles = [title_list[i] for i, lbl in enumerate(labels) if lbl == c]
        cluster_sizes[c] = len(member_titles)
        if not member_titles:
            cluster_labels[c] = f"Cluster {c}"
            continue
        tfidf_matrix = tfidf.transform(member_titles)
        mean_scores = tfidf_matrix.mean(axis=0).A1
        top_idx = mean_scores.argsort()[::-1][:3]
        top_terms = [feature_names[i] for i in top_idx]
        cluster_labels[c] = f"C{c}: {' · '.join(top_terms)}"

    # ── 5. PCA 2D ──────────────────────────────────────────────────────────────
    status.text("Reducing to 2D with PCA...")
    pca = PCA(n_components=2, random_state=42)
    X_2d = pca.fit_transform(X_norm)
    var_explained = pca.explained_variance_ratio_ * 100

    # ── 6. Build scatter dataframe ────────────────────────────────────────────
    import plotly.express as px

    df_plot = pd.DataFrame({
        "PC1":      X_2d[:, 0],
        "PC2":      X_2d[:, 1],
        "Cluster":  [cluster_labels[lbl] for lbl in labels],
        "Accession": acc_list,
        "Title":    [t[:80] for t in title_list],
        "Tech Type": tech_list,
    })

    status.empty()

    # ── 7. Render ──────────────────────────────────────────────────────────────
    # Cluster summary table
    st.subheader("Cluster Summary")
    summary_rows = []
    for c in range(k_clusters):
        member_tech = [tech_list[i] for i, lbl in enumerate(labels) if lbl == c]
        top_tech = Counter(member_tech).most_common(1)
        summary_rows.append({
            "Cluster": cluster_labels[c],
            "Size": cluster_sizes[c],
            "Dominant Tech Type": top_tech[0][0] if top_tech else "—",
        })
    st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

    # PCA scatter
    st.subheader(f"PCA 2D Scatter — {len(acc_list):,} datasets · k={k_clusters}")
    st.caption(
        f"PC1 explains {var_explained[0]:.1f}% variance, PC2 explains {var_explained[1]:.1f}% "
        f"(total {var_explained.sum():.1f}%). Each point is one GSE dataset."
    )
    fig = px.scatter(
        df_plot,
        x="PC1", y="PC2",
        color="Cluster",
        hover_data={"Accession": True, "Title": True, "Tech Type": True, "PC1": False, "PC2": False},
        title=f"K-Means (k={k_clusters}) on {len(acc_list):,} GEO embeddings",
        height=600,
        opacity=0.7,
    )
    fig.update_traces(marker=dict(size=4))
    fig.update_layout(legend=dict(orientation="v", x=1.01, y=1))
    st.plotly_chart(fig, use_container_width=True)

    # WCSS / inertia
    st.metric("K-Means Inertia (WCSS)", f"{km.inertia_:,.0f}")
    st.caption(
        "Lower inertia = tighter clusters. Run with different k values to find the elbow. "
        "Lower inertia = tighter clusters. Run with different k values to find the elbow."
    )

    # Elbow plot option
    if st.checkbox("Show elbow curve (k = 2–15, slow)", key="km_elbow"):
        st.info("Computing K-Means for k=2..15 — this may take 1-2 minutes...")
        inertias = []
        ks = list(range(2, 16))
        elbow_prog = st.progress(0)
        for i, ki in enumerate(ks):
            ki_model = KMeans(n_clusters=ki, init="k-means++", n_init=5, random_state=42)
            ki_model.fit(X_norm)
            inertias.append(ki_model.inertia_)
            elbow_prog.progress((i + 1) / len(ks))
        elbow_prog.empty()
        elbow_df = pd.DataFrame({"k": ks, "WCSS (Inertia)": inertias}).set_index("k")
        st.line_chart(elbow_df)
        st.caption("Choose k at the 'elbow' — where adding more clusters gives diminishing inertia reduction.")


# ── Section 10: Formal Ablation — Table 4.2 style ────────────────────────────

def _section_formal_ablation():
    st.subheader("Formal Ablation Study")
    st.caption(
        "Systematic comparison of retrieval configurations: semantic-only, lexical-only, "
        "hybrid without MeSH, and full hybrid. Evaluates using P@10 / MRR on the 50-query benchmark above "
        "(or a custom query set). **This produces a publishable result table.**"
    )

    # Query set choice
    with st.expander("Query set options"):
        use_full_50 = st.radio(
            "Query set",
            ["Use built-in 50-query set", "Enter custom queries"],
            key="abl_qset",
        )
        if use_full_50 == "Enter custom queries":
            custom_raw = st.text_area(
                "One query per line (format: query | MeSH term for relevance)",
                placeholder="breast cancer RNA-seq | Breast Neoplasms\nlung cancer | Lung Neoplasms",
                height=150,
                key="abl_custom",
            )
            custom_queries = []
            for line in custom_raw.strip().splitlines():
                if "|" in line:
                    q, m = line.split("|", 1)
                    custom_queries.append({"query": q.strip(), "relevant_mesh": [m.strip()]})
            queries_to_eval = custom_queries or IR_EVAL_QUERIES
        else:
            queries_to_eval = IR_EVAL_QUERIES

    k_abl = st.slider("k for P@k", 5, 20, 10, 5, key="abl_k")
    run_abl = st.button("▶ Run Ablation Study", key="run_ablation", type="primary")

    CONFIGS = [
        {"label": "Semantic only",       "semantic": True,  "lexical": False, "mesh": False},
        {"label": "Lexical only",        "semantic": False, "lexical": True,  "mesh": False},
        {"label": "Hybrid (no MeSH)",    "semantic": True,  "lexical": True,  "mesh": False},
        {"label": "Hybrid + MeSH",       "semantic": True,  "lexical": True,  "mesh": True},
    ]

    if not run_abl:
        st.info(
            "Select the query set and k, then click **▶ Run Ablation Study**. "
            "This runs all 4 retrieval configurations × N queries and reports P@k and MRR for each."
        )
        # Show the expected table structure
        st.markdown("""
**Expected output format:**

| Configuration | P@10 | MRR | Avg Latency (ms) |
|---|---|---|---|
| Semantic only | 0.xxx | 0.xxx | — |
| Lexical only | 0.xxx | 0.xxx | — |
| Hybrid (no MeSH) | 0.xxx | 0.xxx | — |
| **Hybrid + MeSH** | **0.xxx** | **0.xxx** | — |
        """)
        return

    from search import HybridSearchEngine

    # For each config, run all queries and compute mean P@k and MRR
    summary_rows = []
    all_per_query: dict[str, list[dict]] = {cfg["label"]: [] for cfg in CONFIGS}
    n_total = len(CONFIGS) * len(queries_to_eval)
    progress = st.progress(0)
    status = st.empty()
    done = 0

    for cfg in CONFIGS:
        for q_entry in queries_to_eval:
            status.text(f"[{done+1}/{n_total}] {cfg['label']} | {q_entry['query']}")
            try:
                with SessionLocal() as db:
                    engine = HybridSearchEngine(db)

                    metrics = _evaluate_query_ir(
                        q_entry["query"], q_entry["relevant_mesh"], engine, db,
                        k=k_abl,
                        use_semantic=cfg["semantic"],
                        use_lexical=cfg["lexical"],
                        use_mesh=cfg["mesh"],
                    )
                    elapsed = metrics["latency_ms"]
                all_per_query[cfg["label"]].append({
                    "query": q_entry["query"],
                    f"P@{k_abl}": metrics["P@10"],
                    "RR": metrics["RR"],
                    "latency_ms": elapsed,
                })
            except Exception as e:
                st.error(f"[{cfg['label']}] '{q_entry['query']}' failed: {type(e).__name__}: {e}")
                all_per_query[cfg["label"]].append({
                    "query": q_entry["query"],
                    f"P@{k_abl}": 0.0,
                    "RR": 0.0,
                    "latency_ms": None,
                })
            done += 1
            progress.progress(done / n_total)

    status.empty()
    progress.empty()

    # Aggregate per config
    for cfg in CONFIGS:
        per_q = all_per_query[cfg["label"]]
        valid = [q for q in per_q if isinstance(q[f"P@{k_abl}"], float)]
        mean_p   = sum(q[f"P@{k_abl}"] for q in valid) / len(valid) if valid else 0
        mean_mrr = sum(q["RR"] for q in valid) / len(valid) if valid else 0
        lats = [q["latency_ms"] for q in valid if q["latency_ms"]]
        mean_lat = sum(lats) / len(lats) if lats else None
        summary_rows.append({
            "Configuration": cfg["label"],
            f"P@{k_abl}": round(mean_p, 3),
            "MRR": round(mean_mrr, 3),
            "Avg Latency (ms)": round(mean_lat) if mean_lat else "—",
            "Queries": len(valid),
        })

    df_abl = pd.DataFrame(summary_rows)
    st.subheader(f"Ablation Results — {len(queries_to_eval)} queries · k={k_abl}")
    st.dataframe(df_abl, use_container_width=True, hide_index=True)

    # Bar chart: P@k and MRR side by side for all configurations
    geo_rows = summary_rows
    chart_df = pd.DataFrame({
        "Configuration": [r["Configuration"] for r in geo_rows],
        f"P@{k_abl}": [r[f"P@{k_abl}"] for r in geo_rows],
        "MRR": [r["MRR"] for r in geo_rows],
    }).set_index("Configuration")
    st.bar_chart(chart_df)

    # Best config callout
    best = max(geo_rows, key=lambda r: r["MRR"])
    st.success(
        f"Best configuration: **{best['Configuration']}** "
        f"— P@{k_abl} = {best[f'P@{k_abl}']:.3f}, MRR = {best['MRR']:.3f}"
    )

    # Per-query breakdown
    with st.expander("Per-query breakdown"):
        all_rows = []
        for cfg in CONFIGS:
            for entry in all_per_query[cfg["label"]]:
                all_rows.append({
                    "Config": cfg["label"],
                    "Query": entry["query"],
                    f"P@{k_abl}": entry[f"P@{k_abl}"],
                    "RR": entry["RR"],
                })
        st.dataframe(pd.DataFrame(all_rows), use_container_width=True, hide_index=True)


# ── main entry point ───────────────────────────────────────────────────────────

def show_analytics_dashboard():
    st.title("Analytics Dashboard")
    st.caption("Database statistics, metadata coverage, MeSH tagging, search benchmarking, and performance profiling.")

    tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9, tab10 = st.tabs([
        "📊 Overview",
        "🔬 Study Types",
        "🧬 Organisms",
        "🏷️ MeSH Coverage",
        "🎯 Search Benchmark",
        "⚡ Pipeline Comparison",
        "⏱️ Performance",
        "📐 IR Evaluation",
        "🗺️ Topic Clusters",
        "🔬 Ablation Study",
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

    with tab8:
        _section_ir_evaluation()

    with tab9:
        _section_kmeans_clustering()

    with tab10:
        _section_formal_ablation()
