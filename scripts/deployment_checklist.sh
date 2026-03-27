#!/bin/bash
# Production Deployment Checklist for Data Ingestion

echo "═══════════════════════════════════════════════════════════════"
echo "GEOSearch Production Deployment Checklist"
echo "═══════════════════════════════════════════════════════════════"
echo ""

# Check 1: .env file
echo "1. Checking environment configuration..."
if [ -f .env ]; then
    if grep -q "NCBI_EMAIL" .env; then
        NCBI_EMAIL=$(grep "NCBI_EMAIL" .env | cut -d= -f2)
        if [ "$NCBI_EMAIL" != "user@example.com" ] && [ ! -z "$NCBI_EMAIL" ]; then
            echo "   ✓ .env file found with valid NCBI_EMAIL"
        else
            echo "   ⚠ NCBI_EMAIL not configured or using default"
            echo "     Set NCBI_EMAIL in .env file (required)"
        fi
    else
        echo "   ⚠ NCBI_EMAIL not found in .env"
        echo "     Add: NCBI_EMAIL=your@email.com"
    fi
else
    echo "   ⚠ .env file not found"
    echo "     Create .env with at minimum: NCBI_EMAIL=your@email.com"
fi
echo ""

# Check 2: Docker availability
echo "2. Checking Docker installation..."
if command -v docker &> /dev/null; then
    DOCKER_VERSION=$(docker --version)
    echo "   ✓ Docker installed: $DOCKER_VERSION"
else
    echo "   ✗ Docker not found"
    echo "     Install Docker: https://docs.docker.com/get-docker/"
    exit 1
fi
echo ""

# Check 3: Docker Compose availability
echo "3. Checking Docker Compose..."
if docker compose version &> /dev/null; then
    COMPOSE_VERSION=$(docker compose version --short)
    echo "   ✓ Docker Compose available: v$COMPOSE_VERSION"
else
    echo "   ⚠ Docker Compose v2 not found"
    echo "     Trying docker-compose (v1)..."
    if command -v docker-compose &> /dev/null; then
        echo "   ✓ Using docker-compose v1"
        COMPOSE_CMD="docker-compose"
    else
        echo "   ✗ Docker Compose not found"
        exit 1
    fi
fi
echo ""

# Check 4: Docker daemon
echo "4. Checking Docker daemon..."
if docker ps &> /dev/null; then
    echo "   ✓ Docker daemon is running"
else
    echo "   ✗ Docker daemon not running"
    echo "     Start Docker Desktop or daemon"
    exit 1
fi
echo ""

# Check 5: Project files
echo "5. Checking required project files..."
REQUIRED_FILES=(
    "docker-compose.prod.yml"
    "Dockerfile"
    "requirements.txt"
    "app.py"
    "streamlit_ingest.py"
    "config.py"
)

MISSING_FILES=0
for file in "${REQUIRED_FILES[@]}"; do
    if [ -f "$file" ]; then
        echo "   ✓ $file"
    else
        echo "   ✗ $file (MISSING)"
        MISSING_FILES=$((MISSING_FILES + 1))
    fi
done

if [ $MISSING_FILES -gt 0 ]; then
    echo ""
    echo "   ✗ Missing $MISSING_FILES required file(s)"
    exit 1
fi
echo ""

# Check 6: Port availability
echo "6. Checking port availability..."
PORTS=(8501 5432 19530)
for port in "${PORTS[@]}"; do
    if command -v lsof &> /dev/null; then
        if lsof -Pi :$port -sTCP:LISTEN -t >/dev/null 2>&1; then
            echo "   ⚠ Port $port is in use (might conflict)"
        else
            echo "   ✓ Port $port available"
        fi
    else
        echo "   ℹ Port $port (skipping check - install lsof for verification)"
    fi
done
echo ""

# Check 7: Disk space
echo "7. Checking disk space..."
AVAILABLE_SPACE=$(df -BG . | tail -1 | awk '{print $4}' | sed 's/G//')
if [ "$AVAILABLE_SPACE" -gt 20 ]; then
    echo "   ✓ Sufficient disk space: ${AVAILABLE_SPACE}GB available"
else
    echo "   ⚠ Low disk space: ${AVAILABLE_SPACE}GB available"
    echo "     Recommendation: At least 20GB free for production"
fi
echo ""

# Check 8: Documentation
echo "8. Checking documentation..."
DOCS=(
    "PRODUCTION_DATA_INGESTION.md"
    "PRODUCTION_INGESTION_SUMMARY.md"
)

for doc in "${DOCS[@]}"; do
    if [ -f "$doc" ]; then
        echo "   ✓ $doc"
    else
        echo "   ⚠ $doc (missing)"
    fi
done
echo ""

echo "═══════════════════════════════════════════════════════════════"
echo "Checklist Complete"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "Next steps:"
echo ""
echo "1. Ensure .env has NCBI_EMAIL set"
echo ""
echo "2. Start the deployment:"
echo "   chmod +x scripts/init_production.sh"
echo "   ./scripts/init_production.sh"
echo ""
echo "3. Once running, access:"
echo "   http://localhost:8501"
echo ""
echo "4. Click '📥 Data Ingestion' in sidebar"
echo ""
echo "5. Enter a search query and start ingestion"
echo ""
echo "For detailed documentation, see:"
echo "   • PRODUCTION_DATA_INGESTION.md (comprehensive guide)"
echo "   • PRODUCTION_INGESTION_SUMMARY.md (quick overview)"
echo ""
