#!/usr/bin/env python3
"""
Download and load the full MeSH (Medical Subject Headings) database from NLM.
This script fetches ~30,000 MeSH descriptors with synonyms and loads them into PostgreSQL.
"""
import argparse
import gzip
import logging
import os
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import requests
from tqdm import tqdm

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db import get_db
from db.models import MeshTerm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# MeSH XML download URL (2024 version - latest available)
# See: https://nlmpubs.nlm.nih.gov/projects/mesh/2024/xmlmesh/
MESH_XML_URL = "https://nlmpubs.nlm.nih.gov/projects/mesh/2024/xmlmesh/desc2024.xml"


def download_mesh_xml(output_path: str, force: bool = False) -> str:
    """
    Download MeSH XML file from NLM.

    Args:
        output_path: Path to save the XML file
        force: Force re-download even if file exists

    Returns:
        Path to downloaded file
    """
    output_file = Path(output_path)

    if output_file.exists() and not force:
        logger.info(f"MeSH XML already exists at {output_file}")
        logger.info(f"File size: {output_file.stat().st_size / 1024 / 1024:.1f} MB")
        return str(output_file)

    logger.info(f"Downloading MeSH data from {MESH_XML_URL}")

    # Determine if URL is for gzipped file
    is_gzipped = MESH_XML_URL.endswith('.gz')

    if is_gzipped:
        logger.info("Downloading gzipped file (~15 MB compressed)...")
    else:
        logger.info("Downloading XML file (~40 MB)...")

    try:
        response = requests.get(MESH_XML_URL, stream=True, timeout=60)
        response.raise_for_status()

        # Get file size for progress bar
        total_size = int(response.headers.get('content-length', 0))

        # Create parent directory if needed
        output_file.parent.mkdir(parents=True, exist_ok=True)

        # Download to temporary file
        temp_file = output_file.with_suffix('.tmp')

        # Download with progress bar
        with open(temp_file, 'wb') as f:
            with tqdm(total=total_size, unit='B', unit_scale=True, desc="Downloading") as pbar:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        pbar.update(len(chunk))

        logger.info(f"Downloaded {temp_file.stat().st_size / 1024 / 1024:.1f} MB")

        # Decompress if gzipped
        if is_gzipped:
            logger.info("Decompressing gzip file...")
            with gzip.open(temp_file, 'rb') as f_in:
                with open(output_file, 'wb') as f_out:
                    f_out.write(f_in.read())
            temp_file.unlink()  # Remove temp gzipped file
            logger.info(f"Decompressed to {output_file}")
        else:
            # Just rename
            temp_file.rename(output_file)

        logger.info(f"File size: {output_file.stat().st_size / 1024 / 1024:.1f} MB")
        return str(output_file)

    except Exception as e:
        logger.error(f"Failed to download MeSH XML: {e}")
        if output_file.exists():
            output_file.unlink()
        raise


def parse_mesh_xml(xml_path: str) -> list[dict]:
    """
    Parse MeSH XML file and extract descriptors.

    Args:
        xml_path: Path to MeSH XML file

    Returns:
        List of MeSH term dictionaries
    """
    logger.info(f"Parsing MeSH XML: {xml_path}")

    tree = ET.parse(xml_path)
    root = tree.getroot()

    descriptors = []

    # Find all DescriptorRecord elements
    descriptor_records = root.findall('.//DescriptorRecord')

    logger.info(f"Found {len(descriptor_records)} MeSH descriptors")

    for record in tqdm(descriptor_records, desc="Parsing descriptors"):
        try:
            # Extract DescriptorUI (MeSH ID)
            descriptor_ui_elem = record.find('.//DescriptorUI')
            if descriptor_ui_elem is None or not descriptor_ui_elem.text:
                continue

            mesh_id = descriptor_ui_elem.text.strip()

            # Extract DescriptorName (Preferred name)
            descriptor_name_elem = record.find('.//DescriptorName/String')
            if descriptor_name_elem is None or not descriptor_name_elem.text:
                continue

            preferred_name = descriptor_name_elem.text.strip()

            # Extract entry terms (synonyms)
            entry_terms = []

            # Look in all Concept/TermList/Term elements
            for concept in record.findall('.//Concept'):
                for term in concept.findall('.//Term'):
                    term_string = term.find('String')
                    if term_string is not None and term_string.text:
                        term_text = term_string.text.strip()
                        # Don't duplicate the preferred name
                        if term_text != preferred_name and term_text not in entry_terms:
                            entry_terms.append(term_text)

            # Extract tree numbers (hierarchy)
            tree_numbers = []
            for tree_num in record.findall('.//TreeNumber'):
                if tree_num.text:
                    tree_numbers.append(tree_num.text.strip())

            descriptors.append({
                'mesh_id': mesh_id,
                'descriptor_ui': mesh_id,  # Same as mesh_id
                'preferred_name': preferred_name,
                'entry_terms': entry_terms if entry_terms else None,
                'tree_numbers': tree_numbers if tree_numbers else None,
            })

        except Exception as e:
            logger.warning(f"Error parsing descriptor: {e}")
            continue

    logger.info(f"Successfully parsed {len(descriptors)} MeSH descriptors")
    return descriptors


def load_mesh_to_db(descriptors: list[dict], batch_size: int = 100, skip_existing: bool = True):
    """
    Load MeSH descriptors into database.

    Args:
        descriptors: List of MeSH term dictionaries
        batch_size: Number of records to commit at once
        skip_existing: Skip terms that already exist
    """
    db = next(get_db())

    logger.info(f"Loading {len(descriptors)} MeSH terms into database")

    if skip_existing:
        # Get existing MeSH IDs
        existing_ids = set(row[0] for row in db.query(MeshTerm.mesh_id).all())
        logger.info(f"Found {len(existing_ids)} existing MeSH terms")

        # Filter out existing
        original_count = len(descriptors)
        descriptors = [d for d in descriptors if d['mesh_id'] not in existing_ids]
        logger.info(f"Skipping {original_count - len(descriptors)} existing terms")
        logger.info(f"Will insert {len(descriptors)} new terms")

    if not descriptors:
        logger.info("No new terms to insert")
        db.close()
        return

    inserted = 0
    errors = 0

    try:
        for i, descriptor in enumerate(tqdm(descriptors, desc="Loading to database")):
            try:
                mesh_term = MeshTerm(
                    mesh_id=descriptor['mesh_id'],
                    descriptor_ui=descriptor['descriptor_ui'],
                    preferred_name=descriptor['preferred_name'],
                    entry_terms=descriptor['entry_terms'],
                    tree_numbers=descriptor['tree_numbers'],
                )

                db.merge(mesh_term)  # Use merge for upsert behavior
                inserted += 1

                # Commit in batches
                if (i + 1) % batch_size == 0:
                    db.commit()
                    logger.debug(f"Committed batch {(i + 1) // batch_size}")

            except Exception as e:
                logger.error(f"Error inserting {descriptor['mesh_id']}: {e}")
                errors += 1
                db.rollback()

        # Final commit
        db.commit()

    except Exception as e:
        logger.error(f"Database error: {e}")
        db.rollback()
        raise
    finally:
        db.close()

    logger.info(f"Loaded {inserted} MeSH terms successfully")
    if errors > 0:
        logger.warning(f"Encountered {errors} errors")


def show_statistics():
    """Show MeSH database statistics."""
    db = next(get_db())

    total = db.query(MeshTerm).count()

    # Count synonyms
    from sqlalchemy import func
    result = db.query(
        func.sum(func.jsonb_array_length(MeshTerm.entry_terms))
    ).filter(MeshTerm.entry_terms.isnot(None)).scalar()
    total_synonyms = int(result) if result else 0

    # Sample terms
    samples = db.query(MeshTerm).limit(10).all()

    db.close()

    print("\n" + "=" * 80)
    print("MeSH DATABASE STATISTICS")
    print("=" * 80)
    print(f"Total MeSH Descriptors: {total:,}")
    print(f"Total Entry Terms (Synonyms): {total_synonyms:,}")
    print(f"Average Synonyms per Term: {total_synonyms / total:.1f}" if total > 0 else "N/A")
    print()
    print("Sample MeSH Terms:")
    print("-" * 80)
    for term in samples:
        syn_count = len(term.entry_terms) if term.entry_terms else 0
        print(f"{term.mesh_id}: {term.preferred_name} ({syn_count} synonyms)")
    print("=" * 80)


def main():
    parser = argparse.ArgumentParser(
        description="Download and load full MeSH database from NLM"
    )
    parser.add_argument(
        "--xml-file",
        type=str,
        default="data/mesh/desc2025.xml",
        help="Path to save/load MeSH XML file (default: data/mesh/desc2025.xml)",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Force re-download even if XML file exists",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip download, use existing XML file",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        default=True,
        help="Skip MeSH terms that already exist in database (default: True)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Database commit batch size (default: 100)",
    )

    args = parser.parse_args()

    try:
        # Step 1: Download MeSH XML
        if not args.skip_download:
            xml_path = download_mesh_xml(args.xml_file, force=args.force_download)
        else:
            xml_path = args.xml_file
            if not Path(xml_path).exists():
                logger.error(f"XML file not found: {xml_path}")
                logger.error("Remove --skip-download to download the file")
                return 1

        # Step 2: Parse MeSH XML
        descriptors = parse_mesh_xml(xml_path)

        # Step 3: Load into database
        load_mesh_to_db(
            descriptors,
            batch_size=args.batch_size,
            skip_existing=args.skip_existing
        )

        # Step 4: Show statistics
        show_statistics()

        logger.info("✓ MeSH database loaded successfully!")
        return 0

    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        return 1
    except Exception as e:
        logger.error(f"Failed to load MeSH database: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
