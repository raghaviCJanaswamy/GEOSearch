#!/bin/bash
# Initialize production GEOSearch deployment with docker-compose

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Load environment variables from .env if it exists
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
    echo -e "${GREEN}✓ Loaded environment from .env${NC}"
else
    echo -e "${YELLOW}⚠ No .env file found. Using defaults.${NC}"
    echo -e "${YELLOW}  Create .env file with: NCBI_EMAIL=your@email.com${NC}"
fi

# Validate NCBI_EMAIL
if [ -z "${NCBI_EMAIL}" ] || [ "${NCBI_EMAIL}" == "user@example.com" ]; then
    echo -e "${RED}✗ NCBI_EMAIL not configured${NC}"
    echo -e "${YELLOW}Please set NCBI_EMAIL in .env file (required for data ingestion)${NC}"
    exit 1
fi

echo -e "${GREEN}Starting GEOSearch production deployment...${NC}"

# Start services
echo -e "${YELLOW}→ Starting Docker services...${NC}"
docker compose -f docker-compose.prod.yml up -d

# Wait for services to be healthy
echo -e "${YELLOW}→ Waiting for services to be ready...${NC}"
sleep 10

# Check service health
echo -e "${YELLOW}→ Checking service health...${NC}"
for service in postgres milvus; do
    container="${service}"
    if [ "$service" == "milvus" ]; then
        container="milvus-standalone"
    elif [ "$service" == "postgres" ]; then
        container="geo_postgres"
    fi
    
    if docker ps --filter "name=$container" --filter "health=healthy" -q 2>/dev/null | grep -q .; then
        echo -e "${GREEN}✓ $service is healthy${NC}"
    else
        echo -e "${YELLOW}⚠ Waiting for $service to be healthy...${NC}"
        sleep 10
    fi
done

# Display status
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}GEOSearch Production Deployment Started${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo ""
echo -e "Services running:"
docker compose -f docker-compose.prod.yml ps
echo ""
echo -e "${GREEN}Access points:${NC}"
echo -e "  • Streamlit App:  ${YELLOW}http://localhost:8501${NC}"
echo -e "  • PostgreSQL:     ${YELLOW}localhost:5432${NC}"
echo -e "  • Milvus Vector:  ${YELLOW}localhost:19530${NC}"
echo ""
echo -e "${GREEN}Next steps:${NC}"
echo -e "  1. Open http://localhost:8501 in your browser"
echo -e "  2. Click '📥 Data Ingestion' in the sidebar"
echo -e "  3. Enter a search query (e.g., 'cancer', 'diabetes')"
echo -e "  4. Click 'Start Ingestion' to begin loading data"
echo ""
echo -e "${YELLOW}To view logs:${NC}"
echo -e "  docker compose -f docker-compose.prod.yml logs -f geosearch-app"
echo ""
echo -e "${YELLOW}To stop services:${NC}"
echo -e "  docker compose -f docker-compose.prod.yml down"
echo ""
