#!/bin/bash
# Quick data ingestion script for common queries

set -e

echo "Quick GEO Data Ingestion"
echo "========================"
echo ""
echo "This script will ingest several common dataset types."
echo "Total: ~200-300 datasets (will take 10-20 minutes)"
echo ""
read -p "Continue? (y/n) " -n 1 -r
echo ""

if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    exit 0
fi

# Activate virtual environment
source venv/bin/activate

# Common queries
queries=(
    "breast cancer RNA-seq:50"
    "single-cell RNA-seq human:50"
    "diabetes gene expression:50"
    "lung cancer microarray:50"
    "mouse liver RNA-seq:50"
)

echo ""
echo "Starting ingestion..."
echo ""

for query_spec in "${queries[@]}"; do
    IFS=':' read -r query count <<< "$query_spec"

    echo "----------------------------------------"
    echo "Query: $query"
    echo "Count: $count"
    echo "----------------------------------------"

    python -m geo_ingest.ingest_pipeline query \
        --query "$query" \
        --retmax "$count" || echo "Warning: Query failed, continuing..."

    echo ""
    sleep 2
done

echo ""
echo "Ingestion complete!"
echo ""
echo "Tagging datasets with MeSH terms..."
python3 << 'EOF'
from db import get_db
from mesh.matcher import tag_all_gse_records

db = next(get_db())
count = tag_all_gse_records(db, confidence_threshold=0.3)
print(f"Created {count} MeSH associations")
EOF

echo ""
echo "Done! Launch the UI with: streamlit run app.py"
