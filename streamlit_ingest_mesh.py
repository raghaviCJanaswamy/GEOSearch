"""
MeSH Term Loader functions for Streamlit app.
"""
import logging
import streamlit as st
from db import get_db, MeshTerm

logger = logging.getLogger(__name__)


def show_mesh_loader() -> None:
    """Display MeSH term loader interface."""
    try:
        db = next(get_db())
        mesh_count = db.query(MeshTerm).count()
        db.close()
    except Exception as e:
        mesh_count = 0

    # Current status
    if mesh_count > 0:
        st.success(f"✅ MeSH Terms Loaded: {mesh_count:,} terms in database")
    else:
        st.warning("⚠️ No MeSH terms loaded yet")

    st.markdown("---")

    # Loading options
    st.markdown("### Load MeSH Terms")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Option 1: Auto Download & Load**")
        st.write(
            "Automatically download the latest MeSH database from NLM and load it."
        )
        if st.button(
            "📥 Download & Load MeSH",
            type="primary",
            use_container_width=True,
            key="load_mesh_auto",
        ):
            load_mesh_full()

    with col2:
        st.markdown("**Option 2: Load from Local File**")
        st.write("Load MeSH terms from an existing desc*.xml file.")

        uploaded_file = st.file_uploader(
            "Choose MeSH XML file",
            type=["xml"],
            key="mesh_file_upload",
            help="Upload a MeSH descriptors XML file (e.g., desc2024.xml)",
        )

        if uploaded_file is not None:
            if st.button(
                "📤 Load from File",
                use_container_width=True,
                key="load_mesh_file",
            ):
                load_mesh_from_file(uploaded_file)

    st.markdown("---")

    # Information
    with st.expander("ℹ️ About MeSH Terms"):
        st.markdown(
            """
        **Medical Subject Headings (MeSH)** is the National Library of Medicine's controlled vocabulary.
        
        **Benefits of Loading MeSH:**
        - 🔍 Enhanced search with medical terminology
        - 🏥 Better dataset discovery for medical/biological queries
        - 🧬 Automatic query expansion with synonyms
        - 📊 Standardized medical subject classification
        
        **Current MeSH Version:** 2024 (from NLM)
        
        **File Size:** ~30,000 descriptors, ~50 MB downloaded, ~10 MB in database
        
        **Download Time:** ~2-5 minutes (depends on internet speed)
        """
        )


def load_mesh_full() -> None:
    """Load MeSH terms by downloading from NLM."""
    progress_container = st.container()

    with progress_container:
        with st.spinner("Loading MeSH terms..."):
            try:
                from mesh.loader import load_mesh_from_xml
                from pathlib import Path
                import requests

                progress_text = st.empty()
                progress_bar = st.progress(0)

                # Check if local file exists
                data_dir = Path("data/mesh")
                data_dir.mkdir(parents=True, exist_ok=True)
                mesh_file = data_dir / "desc2024.xml"

                if not mesh_file.exists():
                    # Download
                    progress_text.info("📥 Downloading MeSH data from NLM...")
                    progress_bar.progress(20)

                    url = "https://nlmpubs.nlm.nih.gov/projects/mesh/2024/xmlmesh/desc2024.xml"
                    response = requests.get(url, stream=True, timeout=30)
                    response.raise_for_status()

                    with open(mesh_file, "wb") as f:
                        f.write(response.content)

                    progress_text.info("✓ Downloaded successfully")
                    progress_bar.progress(50)
                else:
                    progress_text.info("📁 Using existing MeSH file")
                    progress_bar.progress(50)

                # Load into database
                progress_text.info("⚙️ Loading into database...")
                progress_bar.progress(70)

                db = next(get_db())
                count = load_mesh_from_xml(str(mesh_file), db)
                db.close()

                progress_text.info("✓ Processing complete")
                progress_bar.progress(100)

                st.success(f"✅ Successfully loaded {count:,} MeSH terms!")
                st.info(
                    "🎉 MeSH terms are now available for enhanced search and query expansion."
                )

            except Exception as e:
                st.error(f"❌ Failed to load MeSH terms: {str(e)}")
                st.caption(
                    "Make sure you have internet connection and sufficient disk space."
                )


def load_mesh_from_file(uploaded_file) -> None:
    """Load MeSH terms from uploaded file."""
    progress_container = st.container()

    with progress_container:
        with st.spinner("Loading MeSH terms from file..."):
            try:
                from mesh.loader import load_mesh_from_xml
                import tempfile
                from pathlib import Path

                progress_text = st.empty()
                progress_bar = st.progress(0)

                # Save uploaded file temporarily
                with tempfile.TemporaryDirectory() as tmpdir:
                    temp_file = Path(tmpdir) / uploaded_file.name
                    temp_file.write_bytes(uploaded_file.getbuffer())

                    progress_text.info(f"📤 Processing {uploaded_file.name}...")
                    progress_bar.progress(30)

                    # Load into database
                    progress_text.info("⚙️ Loading into database...")
                    progress_bar.progress(60)

                    db = next(get_db())
                    count = load_mesh_from_xml(str(temp_file), db)
                    db.close()

                    progress_text.info("✓ Processing complete")
                    progress_bar.progress(100)

                st.success(f"✅ Successfully loaded {count:,} MeSH terms!")
                st.info(
                    "🎉 MeSH terms are now available for enhanced search and query expansion."
                )

            except Exception as e:
                st.error(f"❌ Failed to load MeSH terms: {str(e)}")
