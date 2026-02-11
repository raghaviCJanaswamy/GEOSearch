#!/bin/bash
# VM Deployment Script for GEOSearch
# Run this on a fresh Ubuntu/Debian VM to set up GEOSearch

set -e

echo "================================"
echo "GEOSearch VM Deployment Script"
echo "================================"
echo ""

# Check if running on appropriate OS
if ! command -v apt-get &> /dev/null; then
    echo "ERROR: This script requires Ubuntu/Debian. Please install manually on other distributions."
    exit 1
fi

# Step 1: Update system
echo "[1/6] Updating system packages..."
sudo apt-get update
sudo apt-get upgrade -y
echo "✓ System updated"
echo ""

# Step 2: Install Docker
echo "[2/6] Installing Docker..."
sudo apt-get install -y docker.io docker-compose-plugin
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker $USER
echo "✓ Docker installed"
echo ""

# Step 3: Create app directory
echo "[3/6] Setting up application directory..."
APP_DIR="${HOME}/geosearch"
mkdir -p "$APP_DIR"
cd "$APP_DIR"
echo "✓ App directory: $APP_DIR"
echo ""

# Step 4: Clone repository or use local one
echo "[4/6] Setting up GEOSearch repository..."
if [ ! -f "docker-compose.yml" ]; then
    # If not already in GEOSearch directory, you can clone:
    # git clone https://github.com/raghaviCJanaswamy/GEOSearch.git .
    # For now, assume files are present
    echo "⚠ Please ensure GEOSearch files are in $APP_DIR"
    echo "  You can copy them with: cp -r /path/to/GEOSearch/* $APP_DIR/"
fi
echo ""

# Step 5: Create .env file
echo "[5/6] Creating environment configuration..."
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
    else
        cat > .env << 'EOF'
POSTGRES_DB=geosearch
POSTGRES_USER=geouser
POSTGRES_PASSWORD=geopass
NCBI_EMAIL=your.email@example.com
NCBI_API_KEY=
EMBEDDING_PROVIDER=local
LOG_LEVEL=INFO
STREAMLIT_SERVER_PORT=8501
STREAMLIT_SERVER_ADDRESS=0.0.0.0
EOF
    fi
    echo "⚠ Created .env file - PLEASE EDIT and set NCBI_EMAIL!"
    echo "  Edit with: nano .env"
fi
echo ""

# Step 6: Build and start containers
echo "[6/6] Building and starting containers..."
docker compose build
docker compose up -d
echo "✓ Containers started"
echo ""

# Wait for services to be healthy
echo "⏳ Waiting for services to be healthy (this may take 1-2 minutes)..."
for i in {1..30}; do
    if docker compose ps | grep -q "healthy"; then
        echo "✓ Services are healthy!"
        break
    fi
    sleep 5
done

echo ""
echo "================================"
echo "Deployment Complete!"
echo "================================"
echo ""
echo "Next Steps:"
echo "1. Edit .env file with your NCBI email:"
echo "   nano $APP_DIR/.env"
echo ""
echo "2. Access the application:"
echo "   http://localhost:8501"
echo ""
echo "3. Initialize database:"
echo "   cd $APP_DIR"
echo "   docker compose exec geosearch-app python -c \"from db import init_db; init_db()\""
echo ""
echo "4. Load MeSH data:"
echo "   docker compose exec geosearch-app python scripts/load_mesh_full.py"
echo ""
echo "5. Ingest sample data:"
echo "   docker compose exec geosearch-app bash scripts/quick_ingest.sh"
echo ""
echo "Useful commands:"
echo "  View logs:       docker compose logs -f geosearch-app"
echo "  Stop services:   docker compose stop"
echo "  Start services:  docker compose start"
echo "  Service status:  docker compose ps"
echo ""
