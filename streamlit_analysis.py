"""
Expression matrix analysis pipeline for GEOSearch.
Fetches GSE data from NCBI GEO and runs QC + DE analysis.
"""
import io
import logging
import os
import tempfile

import numpy as np
import pandas as pd
import streamlit as st

logger = logging.getLogger(__name__)

# ── GEO data fetching ─────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False, ttl=3600)
def fetch_gse(gse_id: str) -> dict:
    """Download and parse a GSE using GEOparse. Returns metadata + expression matrix."""
    import GEOparse
    with tempfile.TemporaryDirectory() as tmpdir:
        gse = GEOparse.get_GEO(geo=gse_id, destdir=tmpdir, silent=True)
        meta = {
            "title":       gse.metadata.get("title", [""])[0],
            "summary":     gse.metadata.get("summary", [""])[0],
            "gpl":         gse.metadata.get("platform_id", [""])[0],
            "organism":    gse.metadata.get("sample_organism", [""])[0],
            "n_samples":   len(gse.gsms),
            "sample_ids":  list(gse.gsms.keys()),
        }

        # Build expression matrix from GSM pivot tables
        frames = []
        sample_info = {}
        for gsm_name, gsm in gse.gsms.items():
            if gsm.table is not None and not gsm.table.empty:
                col = gsm.table.set_index("ID_REF")["VALUE"].rename(gsm_name)
                frames.append(col)
            title  = gsm.metadata.get("title", [""])[0]
            source = gsm.metadata.get("source_name_ch1", [""])[0]
            chars  = gsm.metadata.get("characteristics_ch1", [])
            sample_info[gsm_name] = {"title": title, "source": source, "characteristics": chars}

        expr = pd.concat(frames, axis=1) if frames else pd.DataFrame()
        if not expr.empty:
            expr = expr.apply(pd.to_numeric, errors="coerce").dropna(how="all")

        return {"meta": meta, "expr": expr, "sample_info": sample_info}


# ── QC ────────────────────────────────────────────────────────────────────────

def _qc_section(expr: pd.DataFrame, sample_info: dict):
    import plotly.express as px
    import plotly.graph_objects as go

    st.subheader("Quality Control")

    n_genes, n_samples = expr.shape
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Genes / Probes", f"{n_genes:,}")
    c2.metric("Samples", f"{n_samples:,}")
    c3.metric("Missing Values", f"{expr.isna().sum().sum():,}")
    c4.metric("% Missing", f"{expr.isna().mean().mean()*100:.1f}%")

    tab1, tab2, tab3, tab4 = st.tabs([
        "Expression Distribution", "Sample Correlation", "PCA", "Sample Info"
    ])

    with tab1:
        st.markdown("**Expression value distribution per sample** (box plot)")
        melted = expr.reset_index().melt(id_vars="ID_REF", var_name="Sample", value_name="Expression")
        fig = px.box(melted, x="Sample", y="Expression",
                     title="Expression Distribution per Sample",
                     height=450)
        fig.update_layout(xaxis_tickangle=-45)
        st.plotly_chart(fig, use_container_width=True)

        # Log2 transform option
        if st.checkbox("Apply log2(x+1) transform"):
            expr_log = np.log2(expr.clip(lower=0) + 1)
            melted2 = expr_log.reset_index().melt(id_vars="ID_REF", var_name="Sample", value_name="log2 Expression")
            fig2 = px.box(melted2, x="Sample", y="log2 Expression",
                          title="log2(x+1) Expression Distribution", height=450)
            fig2.update_layout(xaxis_tickangle=-45)
            st.plotly_chart(fig2, use_container_width=True)

    with tab2:
        st.markdown("**Pearson correlation heatmap across samples**")
        corr = expr.corr()
        fig = go.Figure(go.Heatmap(
            z=corr.values,
            x=corr.columns.tolist(),
            y=corr.index.tolist(),
            colorscale="RdBu",
            zmin=-1, zmax=1,
        ))
        fig.update_layout(title="Sample Correlation Heatmap", height=500)
        st.plotly_chart(fig, use_container_width=True)

        # Flag low-correlation samples
        mean_corr = corr.mean()
        outliers = mean_corr[mean_corr < mean_corr.mean() - 2 * mean_corr.std()]
        if not outliers.empty:
            st.warning(f"Potential outlier samples (low mean correlation): {', '.join(outliers.index.tolist())}")
        else:
            st.success("No obvious outlier samples detected.")

    with tab3:
        st.markdown("**PCA — first 2 principal components**")
        from sklearn.decomposition import PCA
        from sklearn.preprocessing import StandardScaler

        expr_clean = expr.fillna(expr.median()).T  # samples × genes
        if expr_clean.shape[0] < 2:
            st.info("Need at least 2 samples for PCA.")
        else:
            scaler = StandardScaler()
            X = scaler.fit_transform(expr_clean)
            n_components = min(5, expr_clean.shape[0], expr_clean.shape[1])
            pca = PCA(n_components=n_components)
            coords = pca.fit_transform(X)
            var_exp = pca.explained_variance_ratio_ * 100

            pca_df = pd.DataFrame({
                "PC1": coords[:, 0],
                "PC2": coords[:, 1],
                "Sample": expr_clean.index,
            })
            fig = px.scatter(pca_df, x="PC1", y="PC2", text="Sample",
                             title=f"PCA — PC1 ({var_exp[0]:.1f}%) vs PC2 ({var_exp[1]:.1f}%)",
                             height=450)
            fig.update_traces(textposition="top center")
            st.plotly_chart(fig, use_container_width=True)

            # Scree plot
            scree_df = pd.DataFrame({"PC": [f"PC{i+1}" for i in range(n_components)],
                                     "Variance Explained (%)": var_exp})
            st.bar_chart(scree_df.set_index("PC"))

    with tab4:
        info_rows = []
        for sid, info in sample_info.items():
            info_rows.append({
                "Sample ID": sid,
                "Title": info["title"],
                "Source": info["source"],
                "Characteristics": " | ".join(info["characteristics"]),
            })
        st.dataframe(pd.DataFrame(info_rows), use_container_width=True, hide_index=True)


# ── DE analysis ───────────────────────────────────────────────────────────────

def _de_section(expr: pd.DataFrame, sample_info: dict):
    import plotly.express as px

    st.subheader("Differential Expression Analysis")
    st.caption("Assign samples to two groups, then run Welch's t-test + volcano plot.")

    samples = list(expr.columns)
    if len(samples) < 4:
        st.warning("Need at least 4 samples for DE analysis.")
        return

    col1, col2 = st.columns(2)
    with col1:
        group_a = st.multiselect("Group A (e.g. control)", samples,
                                  default=samples[:len(samples)//2],
                                  key="de_group_a")
    with col2:
        group_b = st.multiselect("Group B (e.g. treatment)", samples,
                                  default=samples[len(samples)//2:],
                                  key="de_group_b")

    fc_thresh  = st.slider("Log2 Fold-Change threshold", 0.5, 3.0, 1.0, 0.25)
    pval_thresh = st.slider("p-value threshold", 0.001, 0.1, 0.05, 0.005)
    apply_log2  = st.checkbox("Apply log2(x+1) before DE", value=True, key="de_log2")

    if not group_a or not group_b:
        st.info("Select samples for both groups to run DE analysis.")
        return

    overlap = set(group_a) & set(group_b)
    if overlap:
        st.error(f"Samples in both groups: {overlap}")
        return

    if st.button("Run DE Analysis"):
        from scipy import stats

        mat = expr.copy()
        if apply_log2:
            mat = np.log2(mat.clip(lower=0) + 1)
        mat = mat.fillna(mat.median())

        A = mat[group_a].values
        B = mat[group_b].values

        with st.spinner("Running Welch's t-test on all genes..."):
            t_stat, p_vals = stats.ttest_ind(B, A, axis=1, equal_var=False)

        from statsmodels.stats.multitest import multipletests
        _, fdr, _, _ = multipletests(p_vals, method="fdr_bh")

        mean_a = A.mean(axis=1)
        mean_b = B.mean(axis=1)
        log2fc = mean_b - mean_a  # already log2 if apply_log2

        de_df = pd.DataFrame({
            "Gene": expr.index,
            "log2FC": log2fc,
            "p_value": p_vals,
            "FDR": fdr,
            "mean_A": mean_a,
            "mean_B": mean_b,
        }).sort_values("FDR")

        # Significance labels
        de_df["Significant"] = "Not significant"
        de_df.loc[(de_df["FDR"] < pval_thresh) & (de_df["log2FC"] >  fc_thresh), "Significant"] = "Up in B"
        de_df.loc[(de_df["FDR"] < pval_thresh) & (de_df["log2FC"] < -fc_thresh), "Significant"] = "Down in B"

        up   = (de_df["Significant"] == "Up in B").sum()
        down = (de_df["Significant"] == "Down in B").sum()
        c1, c2, c3 = st.columns(3)
        c1.metric("Upregulated in B",   up)
        c2.metric("Downregulated in B", down)
        c3.metric("Total DE genes",     up + down)

        # Volcano plot
        de_df["-log10(FDR)"] = -np.log10(de_df["FDR"].clip(lower=1e-300))
        fig = px.scatter(
            de_df, x="log2FC", y="-log10(FDR)",
            color="Significant",
            color_discrete_map={"Up in B": "red", "Down in B": "blue", "Not significant": "lightgrey"},
            hover_data=["Gene", "FDR", "log2FC"],
            title=f"Volcano Plot — Group B vs Group A  (FDR<{pval_thresh}, |log2FC|>{fc_thresh})",
            height=500,
        )
        fig.add_vline(x= fc_thresh,  line_dash="dash", line_color="grey")
        fig.add_vline(x=-fc_thresh,  line_dash="dash", line_color="grey")
        fig.add_hline(y=-np.log10(pval_thresh), line_dash="dash", line_color="grey")
        st.plotly_chart(fig, use_container_width=True)

        # Top DE genes table
        sig = de_df[de_df["Significant"] != "Not significant"].head(50)
        st.markdown(f"**Top DE genes (showing up to 50 of {up+down})**")
        st.dataframe(
            sig[["Gene","log2FC","p_value","FDR","Significant"]].style.format({
                "log2FC": "{:.3f}", "p_value": "{:.2e}", "FDR": "{:.2e}"
            }),
            use_container_width=True, hide_index=True
        )

        # Heatmap of top 30 DE genes
        top30 = sig.head(30)["Gene"].tolist()
        if top30:
            hm_data = mat.loc[top30]
            import plotly.graph_objects as go
            fig_hm = go.Figure(go.Heatmap(
                z=hm_data.values,
                x=hm_data.columns.tolist(),
                y=hm_data.index.tolist(),
                colorscale="RdBu_r",
            ))
            fig_hm.update_layout(title="Top 30 DE Genes Heatmap", height=600)
            st.plotly_chart(fig_hm, use_container_width=True)

        # Download
        csv = de_df.to_csv(index=False)
        st.download_button("Download DE Results (CSV)", data=csv,
                           file_name=f"DE_results.csv", mime="text/csv")


# ── main entry point ──────────────────────────────────────────────────────────

def show_analysis_pipeline():
    st.title("Expression Matrix Analysis")
    st.caption("Fetch a GEO dataset and run QC + Differential Expression analysis.")

    # GSE input
    col1, col2 = st.columns([3, 1])
    with col1:
        gse_id = st.text_input("Enter GSE Accession", placeholder="e.g. GSE12345",
                                value=st.session_state.get("analysis_gse_id", "")).strip().upper()
    with col2:
        st.markdown("<br>", unsafe_allow_html=True)
        fetch = st.button("Fetch Dataset", type="primary")

    if gse_id:
        st.session_state["analysis_gse_id"] = gse_id

    if not gse_id:
        st.info("Enter a GSE accession number to begin. You can copy one from the Search results.")
        return

    if not gse_id.startswith("GSE"):
        st.error("Accession must start with GSE (e.g. GSE12345)")
        return

    with st.spinner(f"Fetching {gse_id} from NCBI GEO (may take 1–2 minutes for large datasets)..."):
        try:
            data = fetch_gse(gse_id)
        except Exception as e:
            st.error(f"Failed to fetch {gse_id}: {e}")
            st.info("Make sure the accession is valid and your network can reach NCBI GEO FTP.")
            return

    meta = data["meta"]
    expr = data["expr"]
    sample_info = data["sample_info"]

    # Dataset summary
    st.success(f"Loaded **{gse_id}**: {meta['title']}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Samples", meta["n_samples"])
    c2.metric("Platform", meta["gpl"] or "N/A")
    c3.metric("Organism", meta["organism"] or "N/A")
    c4.metric("Genes/Probes", f"{expr.shape[0]:,}" if not expr.empty else "N/A")

    with st.expander("Dataset Summary"):
        st.write(meta["summary"])

    if expr.empty:
        st.warning("No expression matrix available for this dataset. It may be a sequencing study — raw counts are not available via GEOparse soft files.")
        st.info("Try a microarray study (e.g. GSE2034, GSE7390) or an RNA-seq study that deposited processed counts.")
        return

    # Analysis tabs
    tab_qc, tab_de = st.tabs(["QC Analysis", "Differential Expression"])

    with tab_qc:
        _qc_section(expr, sample_info)

    with tab_de:
        _de_section(expr, sample_info)
