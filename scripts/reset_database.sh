#!/bin/bash
# Reset database (WARNING: Deletes all data!)

set -e

echo "========================================="
echo "WARNING: Database Reset"
echo "========================================="
echo ""
echo "This will DELETE ALL DATA including:"
echo "- All ingested GEO datasets"
echo "- All MeSH terms and associations"
echo "- All embeddings in Milvus"
echo ""
read -p "Are you sure? Type 'yes' to continue: " confirm

if [ "$confirm" != "yes" ]; then
    echo "Aborted."
    exit 0
fi

echo ""
echo "Stopping services..."
docker-compose down -v

echo ""
echo "Starting services..."
docker-compose up -d

echo ""
echo "Waiting for services..."
sleep 15

echo ""
echo "Reinitializing database..."
source venv/bin/activate
python -m geo_ingest.ingest_pipeline init

echo ""
echo "Loading sample MeSH data..."
python -m mesh.loader --sample

echo ""
echo "Reset complete. Database is empty and ready for ingestion."
