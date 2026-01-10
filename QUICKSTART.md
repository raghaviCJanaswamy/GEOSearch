# GEOSearch Quick Start Guide

Get up and running with GEOSearch in 10 minutes.

## Prerequisites

- Docker and Docker Compose installed
- Python 3.11+ installed
- 8GB RAM minimum

## Step-by-Step Setup

### 1. Environment Setup (2 minutes)

```bash
# Copy environment template
cp .env.example .env

# Edit .env and set your NCBI email (REQUIRED)
nano .env  # or use your preferred editor

# Find this line and replace with your email:
# NCBI_EMAIL=your.email@example.com
```

### 2. Automated Setup (5 minutes)

```bash
# Run the setup script (handles everything)
bash scripts/setup.sh

# OR use Make:
make setup
```

This script will:
- Create virtual environment
- Install Python dependencies
- Start Docker services (PostgreSQL, Milvus, etc.)
- Initialize database
- Load sample MeSH data

### 3. Ingest Sample Data (2 minutes)

```bash
# Activate virtual environment
source venv/bin/activate

# Ingest 50 breast cancer RNA-seq datasets
python -m geo_ingest.ingest_pipeline query \
    --query "breast cancer RNA-seq" \
    --retmax 50

# OR use quick ingestion script for multiple categories
bash scripts/quick_ingest.sh
```

### 4. Launch UI (1 minute)

```bash
# Start Streamlit interface
streamlit run app.py

# OR use Make:
make ui
```

Open your browser to: http://localhost:8501

## Your First Search

1. Enter a query: `breast cancer RNA-seq`
2. Apply filters (optional):
   - Organism: Homo sapiens
   - Tech type: rna-seq
   - Date range: 2020-2024
3. Click "Search"
4. Explore results with MeSH term highlights

## Quick Commands

```bash
# View database stats
python scripts/db_info.py

# Tag datasets with MeSH
make tag-mesh

# Ingest specific datasets
python -m geo_ingest.ingest_pipeline accessions GSE123456 GSE123457

# Check service status
docker-compose ps

# View logs
docker-compose logs -f
```

## Troubleshooting

### Services not starting?

```bash
# Check Docker is running
docker ps

# Restart services
make restart

# View logs
make logs
```

### NCBI errors?

Make sure you set `NCBI_EMAIL` in `.env`. NCBI requires a valid email.

### Import errors?

```bash
# Reinstall dependencies
source venv/bin/activate
pip install -r requirements.txt
```

### Milvus connection issues?

```bash
# Restart Milvus
docker-compose restart milvus

# Wait 30 seconds for startup
sleep 30
```

The system will gracefully degrade to lexical-only search if Milvus is down.

## Next Steps

- Read the full [README.md](README.md) for advanced features
- Explore the Python API for custom searches
- Load full MeSH descriptors for better term matching
- Ingest more datasets from different domains

## Getting Help

- Check logs: `docker-compose logs`
- View database stats: `python scripts/db_info.py`
- Run tests: `pytest`
- See all make commands: `make help`

## Quick Reference

| Command | Description |
|---------|-------------|
| `make setup` | Initial setup |
| `make start` | Start services |
| `make stop` | Stop services |
| `make ui` | Launch Streamlit UI |
| `make ingest` | Quick data ingestion |
| `make test` | Run tests |
| `make help` | Show all commands |

Enjoy searching GEO datasets! 🧬
