#!/bin/bash
# Setup script for GEOSearch

set -e

echo "========================================="
echo "GEOSearch Setup Script"
echo "========================================="
echo ""

# Check Python version
echo "Checking Python version..."
python_version=$(python3 --version 2>&1 | awk '{print $2}')
echo "Found Python $python_version"

# Check if Python 3.11+
if ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"; then
    echo "Error: Python 3.11+ required"
    exit 1
fi

# Create virtual environment
echo ""
echo "Creating virtual environment..."
python3 -m venv venv
source venv/bin/activate

# Install dependencies
echo ""
echo "Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Check .env
echo ""
if [ ! -f .env ]; then
    echo "Creating .env from template..."
    cp .env.example .env
    echo "⚠️  WARNING: Please edit .env and set NCBI_EMAIL before proceeding!"
else
    echo ".env file already exists"
fi

# Start docker services
echo ""
echo "Starting Docker services..."
docker compose up -d

# Wait for services
echo ""
echo "Waiting for services to be ready..."
sleep 10

# Check service health
echo ""
echo "Checking service health..."
docker compose ps

# Initialize database
echo ""
echo "Initializing database..."
python -m geo_ingest.ingest_pipeline init

# Load sample MeSH data
echo ""
echo "Loading sample MeSH data..."
python -m mesh.loader --sample

echo ""
echo "========================================="
echo "Setup Complete!"
echo "========================================="
echo ""
echo "Next steps:"
echo "1. Edit .env and set NCBI_EMAIL=your.email@example.com"
echo "2. Ingest some data:"
echo "   python -m geo_ingest.ingest_pipeline query --query 'breast cancer RNA-seq' --retmax 50"
echo "3. Launch UI:"
echo "   streamlit run app.py"
echo ""
echo "For full MeSH data, download desc2026.xml and run:"
echo "   python -m mesh.loader --file desc2026.xml"
echo ""
