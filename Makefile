# Makefile for GEOSearch

.PHONY: help setup start stop reset ingest test clean format lint

help:
	@echo "GEOSearch - Available Commands"
	@echo "=============================="
	@echo ""
	@echo "Setup:"
	@echo "  make setup          - Initial project setup"
	@echo "  make install        - Install Python dependencies"
	@echo ""
	@echo "Services:"
	@echo "  make start          - Start all services (docker-compose)"
	@echo "  make stop           - Stop all services"
	@echo "  make restart        - Restart all services"
	@echo "  make logs           - View service logs"
	@echo "  make ps             - Show service status"
	@echo ""
	@echo "Database:"
	@echo "  make init-db        - Initialize database tables"
	@echo "  make reset          - Reset database (deletes all data!)"
	@echo "  make psql           - Connect to PostgreSQL"
	@echo ""
	@echo "Data:"
	@echo "  make load-mesh      - Load sample MeSH data"
	@echo "  make ingest         - Quick data ingestion"
	@echo "  make tag-mesh       - Tag datasets with MeSH terms"
	@echo ""
	@echo "Development:"
	@echo "  make test           - Run tests"
	@echo "  make format         - Format code (black + isort)"
	@echo "  make lint           - Run linters"
	@echo "  make clean          - Clean temporary files"
	@echo ""
	@echo "Application:"
	@echo "  make ui             - Start Streamlit UI"
	@echo ""

setup:
	@echo "Running setup script..."
	@bash scripts/setup.sh

install:
	pip install -r requirements.txt

start:
	docker-compose up -d

stop:
	docker-compose down

restart:
	docker-compose restart

logs:
	docker-compose logs -f

ps:
	docker-compose ps

init-db:
	python -m geo_ingest.ingest_pipeline init

reset:
	@bash scripts/reset_database.sh

psql:
	docker-compose exec postgres psql -U geouser -d geosearch

load-mesh:
	python -m mesh.loader --sample

ingest:
	@bash scripts/quick_ingest.sh

tag-mesh:
	@python3 -c "from db import get_db; from mesh.matcher import tag_all_gse_records; \
	db = next(get_db()); count = tag_all_gse_records(db); print(f'Tagged {count} associations')"

test:
	pytest -v

test-cov:
	pytest --cov=. --cov-report=html --cov-report=term

format:
	black .
	isort .

lint:
	mypy .
	black --check .
	isort --check .

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .mypy_cache htmlcov .coverage

ui:
	streamlit run app.py

# Ingestion commands
ingest-query:
	@read -p "Enter search query: " query; \
	read -p "Number of results (default 50): " count; \
	count=$${count:-50}; \
	python -m geo_ingest.ingest_pipeline query --query "$$query" --retmax $$count

ingest-accessions:
	@read -p "Enter GSE accessions (space-separated): " accessions; \
	python -m geo_ingest.ingest_pipeline accessions $$accessions

# Development server
dev:
	PYTHONPATH=. streamlit run app.py --server.runOnSave true
