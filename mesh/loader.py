"""
MeSH descriptor loader.
Loads MeSH terms from ASCII or XML files into the database.
"""
import argparse
import logging
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session
from tqdm import tqdm

from db import MeshTerm, get_db, init_db

logger = logging.getLogger(__name__)


def load_mesh_from_xml(file_path: str, db: Session) -> int:
    """
    Load MeSH descriptors from XML file (desc2026.xml format).

    Args:
        file_path: Path to MeSH XML file
        db: Database session

    Returns:
        Number of terms loaded

    The MeSH XML file can be downloaded from:
    https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/xmlmesh/desc2026.xml
    """
    logger.info(f"Loading MeSH descriptors from {file_path}")

    if not Path(file_path).exists():
        raise FileNotFoundError(f"MeSH file not found: {file_path}")

    # Parse XML
    tree = ET.parse(file_path)
    root = tree.getroot()

    descriptors = root.findall(".//DescriptorRecord")
    logger.info(f"Found {len(descriptors)} descriptors")

    count = 0
    batch_size = 1000
    batch = []

    for desc in tqdm(descriptors, desc="Loading MeSH terms"):
        # Get descriptor UI and name
        descriptor_ui = desc.find(".//DescriptorUI")
        descriptor_name = desc.find(".//DescriptorName/String")

        if descriptor_ui is None or descriptor_name is None:
            continue

        mesh_id = descriptor_ui.text
        preferred_name = descriptor_name.text

        # Get entry terms (synonyms)
        entry_terms = []
        concepts = desc.findall(".//Concept")
        for concept in concepts:
            terms = concept.findall(".//Term/String")
            for term in terms:
                if term.text and term.text != preferred_name:
                    entry_terms.append(term.text)

        # Get tree numbers (hierarchy)
        tree_numbers = []
        tree_number_elems = desc.findall(".//TreeNumber")
        for tn in tree_number_elems:
            if tn.text:
                tree_numbers.append(tn.text)

        # Create MeshTerm object
        mesh_term = MeshTerm(
            mesh_id=mesh_id,
            descriptor_ui=mesh_id,
            preferred_name=preferred_name,
            entry_terms=entry_terms,
            tree_numbers=tree_numbers,
        )

        batch.append(mesh_term)

        # Commit in batches
        if len(batch) >= batch_size:
            db.bulk_save_objects(batch)
            db.commit()
            count += len(batch)
            batch = []

    # Commit remaining
    if batch:
        db.bulk_save_objects(batch)
        db.commit()
        count += len(batch)

    logger.info(f"Loaded {count} MeSH terms")
    return count


def load_mesh_sample_data(db: Session) -> int:
    """
    Load a small sample of common biomedical MeSH terms for testing.
    Use this if you don't have the full MeSH file.

    Args:
        db: Database session

    Returns:
        Number of terms loaded
    """
    logger.info("Loading sample MeSH terms")

    # Sample of common biomedical terms
    sample_terms = [
        {
            "mesh_id": "D001943",
            "preferred_name": "Breast Neoplasms",
            "entry_terms": ["Breast Cancer", "Mammary Cancer", "Breast Tumor", "Mammary Carcinoma"],
            "tree_numbers": ["C04.588.180", "C17.800.090.500"],
        },
        {
            "mesh_id": "D008175",
            "preferred_name": "Lung Neoplasms",
            "entry_terms": ["Lung Cancer", "Pulmonary Cancer", "Lung Tumor"],
            "tree_numbers": ["C04.588.894.797.520", "C08.381.540"],
        },
        {
            "mesh_id": "D012313",
            "preferred_name": "RNA",
            "entry_terms": ["Ribonucleic Acid", "RNA Molecules"],
            "tree_numbers": ["D13.444.735"],
        },
        {
            "mesh_id": "D017423",
            "preferred_name": "Sequence Analysis, RNA",
            "entry_terms": ["RNA-Seq", "RNA Sequencing", "Transcriptome Sequencing"],
            "tree_numbers": ["E05.393.620.700"],
        },
        {
            "mesh_id": "D059014",
            "preferred_name": "High-Throughput Nucleotide Sequencing",
            "entry_terms": ["Next-Generation Sequencing", "NGS", "Massively Parallel Sequencing"],
            "tree_numbers": ["E05.393.620.500"],
        },
        {
            "mesh_id": "D020869",
            "preferred_name": "Gene Expression Profiling",
            "entry_terms": ["Expression Profiling", "Transcriptional Profiling"],
            "tree_numbers": ["E05.393.420"],
        },
        {
            "mesh_id": "D008657",
            "preferred_name": "Metabolic Diseases",
            "entry_terms": ["Metabolic Disorder", "Metabolism Disorders"],
            "tree_numbers": ["C18.452"],
        },
        {
            "mesh_id": "D003920",
            "preferred_name": "Diabetes Mellitus",
            "entry_terms": ["Diabetes", "Diabetes Mellitus"],
            "tree_numbers": ["C18.452.394.750", "C19.246"],
        },
        {
            "mesh_id": "D009369",
            "preferred_name": "Neoplasms",
            "entry_terms": ["Cancer", "Tumor", "Malignancy", "Cancers", "Tumors"],
            "tree_numbers": ["C04"],
        },
        {
            "mesh_id": "D006801",
            "preferred_name": "Humans",
            "entry_terms": ["Human", "Homo sapiens"],
            "tree_numbers": ["B01.050.150.900.649.313.988.400.112.400.400"],
        },
        {
            "mesh_id": "D051379",
            "preferred_name": "Mice",
            "entry_terms": ["Mouse", "Mus musculus"],
            "tree_numbers": ["B01.050.150.900.649.313.992.635.505.500"],
        },
        {
            "mesh_id": "D016513",
            "preferred_name": "Mice, Inbred C57BL",
            "entry_terms": ["C57BL Mice", "C57BL/6", "C57 Black"],
            "tree_numbers": ["B01.050.150.900.649.313.992.635.505.500.850"],
        },
        {
            "mesh_id": "D020411",
            "preferred_name": "Oligonucleotide Array Sequence Analysis",
            "entry_terms": ["Microarray", "Gene Chip", "DNA Microarray", "Microarray Analysis"],
            "tree_numbers": ["E05.393.625"],
        },
        {
            "mesh_id": "D059010",
            "preferred_name": "Single-Cell Analysis",
            "entry_terms": ["Single Cell", "Single-Cell", "Single Cell Analysis"],
            "tree_numbers": ["E05.200.750"],
        },
        {
            "mesh_id": "D002455",
            "preferred_name": "Cell Division",
            "entry_terms": ["Cell Cycle", "Mitosis"],
            "tree_numbers": ["G04.299"],
        },
    ]

    for term_data in tqdm(sample_terms, desc="Loading sample terms"):
        mesh_term = MeshTerm(**term_data)
        db.merge(mesh_term)  # Use merge to avoid conflicts

    db.commit()

    logger.info(f"Loaded {len(sample_terms)} sample MeSH terms")
    return len(sample_terms)


def main() -> int:
    """CLI entrypoint for loading MeSH data."""
    parser = argparse.ArgumentParser(description="Load MeSH descriptors into database")

    parser.add_argument(
        "--file",
        "-f",
        help="Path to MeSH XML file (desc20XX.xml)",
    )
    parser.add_argument(
        "--sample",
        action="store_true",
        help="Load sample data for testing (no file needed)",
    )
    parser.add_argument(
        "--init-db",
        action="store_true",
        help="Initialize database before loading",
    )

    args = parser.parse_args()

    if not args.file and not args.sample:
        parser.print_help()
        print("\nError: Provide either --file or --sample")
        return 1

    # Initialize database if requested
    if args.init_db:
        logger.info("Initializing database...")
        init_db()

    # Get database session
    db_gen = get_db()
    db = next(db_gen)

    try:
        if args.sample:
            count = load_mesh_sample_data(db)
        else:
            count = load_mesh_from_xml(args.file, db)

        print(f"\nSuccessfully loaded {count} MeSH terms")
        return 0

    except Exception as e:
        logger.error(f"Failed to load MeSH data: {e}", exc_info=True)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
