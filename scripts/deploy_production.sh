#!/bin/bash
# Production Deployment Script for GEOSearch
# Usage: bash deploy_production.sh <vm-ip> <domain-optional>
# Example: bash deploy_production.sh 192.168.1.100 geosearch.example.com

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get inputs
if [ -z "$1" ]; then
    echo -e "${RED}Usage: bash deploy_production.sh <vm-ip> [domain]${NC}"
    echo "Example: bash deploy_production.sh 192.168.1.100 geosearch.example.com"
    exit 1
fi

VM_IP=$1
DOMAIN=${2:-}
DEPLOYMENT_DIR="${HOME}/geosearch-production"

echo -e "${BLUE}================================${NC}"
echo -e "${BLUE}GEOSearch Production Deployment${NC}"
echo -e "${BLUE}================================${NC}"
echo ""
echo -e "${YELLOW}Configuration:${NC}"
echo "  VM IP: $VM_IP"
echo "  Domain: ${DOMAIN:-Not set (use IP directly)}"
echo "  Deployment Dir: $DEPLOYMENT_DIR"
echo ""

# Verify SSH connection
echo -e "${BLUE}[1/8] Verifying SSH connection...${NC}"
if ! ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@$VM_IP "echo 'SSH OK'" > /dev/null 2>&1; then
    echo -e "${RED}ERROR: Cannot SSH to $VM_IP${NC}"
    echo "Make sure:"
    echo "  1. VM is running and accessible"
    echo "  2. You have SSH key configured"
    echo "  3. You can run: ssh root@$VM_IP"
    exit 1
fi
echo -e "${GREEN}✓ SSH connection verified${NC}"
echo ""

# Install Docker on remote VM
echo -e "${BLUE}[2/8] Installing Docker on VM...${NC}"
ssh root@$VM_IP << 'DOCKER_INSTALL'
    apt-get update
    apt-get install -y docker.io docker-compose-plugin
    systemctl start docker
    systemctl enable docker
    usermod -aG docker root
    docker --version
DOCKER_INSTALL
echo -e "${GREEN}✓ Docker installed${NC}"
echo ""

# Transfer GEOSearch files
echo -e "${BLUE}[3/8] Transferring GEOSearch files to VM...${NC}"
ssh root@$VM_IP "mkdir -p $DEPLOYMENT_DIR"
scp -r \
    docker-compose.prod.yml \
    Dockerfile \
    .dockerignore \
    requirements.txt \
    config.py \
    app.py \
    db/ \
    geo_ingest/ \
    mesh/ \
    search/ \
    vector/ \
    scripts/ \
    root@$VM_IP:$DEPLOYMENT_DIR/
echo -e "${GREEN}✓ Files transferred${NC}"
echo ""

# Create environment file
echo -e "${BLUE}[4/8] Creating environment configuration...${NC}"
cat > /tmp/geosearch.env << EOF
# PostgreSQL Configuration
POSTGRES_DB=geosearch
POSTGRES_USER=geouser
POSTGRES_PASSWORD=$(openssl rand -base64 32 | tr -d '=+/' | cut -c1-25)

# NCBI Configuration (REQUIRED)
NCBI_EMAIL=your.email@example.com
NCBI_API_KEY=

# Embedding Configuration
EMBEDDING_PROVIDER=local
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2

# Search Configuration
SEMANTIC_TOP_K=100
LEXICAL_TOP_K=100
FINAL_TOP_K=50
RRF_K=60

# Logging
LOG_LEVEL=INFO

# Streamlit Configuration
STREAMLIT_SERVER_PORT=8501
STREAMLIT_SERVER_ADDRESS=0.0.0.0
EOF

scp /tmp/geosearch.env root@$VM_IP:$DEPLOYMENT_DIR/.env
echo -e "${GREEN}✓ Environment file created${NC}"
echo ""

# Create .env from example and setup configuration
echo -e "${BLUE}[5/8] Setting up configuration on VM...${NC}"
ssh root@$VM_IP << EOF
    cd $DEPLOYMENT_DIR
    
    # Build application image
    docker compose -f docker-compose.prod.yml build --no-cache
    
    # Create data and logs directories
    mkdir -p data logs
    chmod 777 data logs
EOF
echo -e "${GREEN}✓ Configuration completed${NC}"
echo ""

# Start services
echo -e "${BLUE}[6/8] Starting services...${NC}"
ssh root@$VM_IP << EOF
    cd $DEPLOYMENT_DIR
    
    # Start all services
    docker compose -f docker-compose.prod.yml up -d
    
    # Wait for health checks
    for i in {1..60}; do
        if docker compose -f docker-compose.prod.yml exec postgres pg_isready -U geouser > /dev/null 2>&1 && \
           docker compose -f docker-compose.prod.yml exec milvus curl -s http://localhost:9091/healthz > /dev/null 2>&1; then
            echo "Services are healthy"
            break
        fi
        sleep 2
    done
    
    # Show status
    docker compose -f docker-compose.prod.yml ps
EOF
echo -e "${GREEN}✓ Services started${NC}"
echo ""

# Initialize database
echo -e "${BLUE}[7/8] Initializing database...${NC}"
ssh root@$VM_IP << EOF
    cd $DEPLOYMENT_DIR
    
    # Wait for services to be fully ready
    sleep 30
    
    # Initialize database
    docker compose -f docker-compose.prod.yml exec -T geosearch-app python -c "
from db import init_db
print('Initializing database...')
init_db()
print('✓ Database initialized')
" || echo "Database already initialized"
EOF
echo -e "${GREEN}✓ Database initialized${NC}"
echo ""

# Setup reverse proxy if domain provided
echo -e "${BLUE}[8/8] Finalizing setup...${NC}"
if [ -n "$DOMAIN" ]; then
    echo -e "${YELLOW}Setting up SSL with Let's Encrypt...${NC}"
    ssh root@$VM_IP << EOF
        apt-get install -y certbot python3-certbot-nginx
        certbot certonly --standalone -d $DOMAIN --agree-tos --email admin@$DOMAIN --non-interactive
        
        # Copy nginx config
        cat > /etc/nginx/sites-available/geosearch << 'NGINX_CONFIG'
upstream geosearch {
    server 127.0.0.1:8501;
}

server {
    listen 80;
    server_name $DOMAIN;
    return 301 https://\$server_name\$request_uri;
}

server {
    listen 443 ssl http2;
    server_name $DOMAIN;
    
    ssl_certificate /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
    
    add_header Strict-Transport-Security "max-age=31536000" always;
    
    location / {
        proxy_pass http://geosearch;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
NGINX_CONFIG
        
        ln -sf /etc/nginx/sites-available/geosearch /etc/nginx/sites-enabled/
        nginx -t && systemctl reload nginx
        systemctl enable nginx
EOF
    echo -e "${GREEN}✓ SSL configured${NC}"
fi

echo ""
echo -e "${GREEN}================================${NC}"
echo -e "${GREEN}Deployment Successful!${NC}"
echo -e "${GREEN}================================${NC}"
echo ""

if [ -n "$DOMAIN" ]; then
    echo -e "${YELLOW}Access your application:${NC}"
    echo "  https://$DOMAIN"
else
    echo -e "${YELLOW}Access your application:${NC}"
    echo "  http://$VM_IP:8501"
fi

echo ""
echo -e "${YELLOW}Next Steps:${NC}"
echo "1. SSH into VM:"
echo "   ssh root@$VM_IP"
echo ""
echo "2. Edit environment file with your NCBI email:"
echo "   nano $DEPLOYMENT_DIR/.env"
echo "   (Set NCBI_EMAIL=your.email@example.com)"
echo ""
echo "3. Restart app:"
echo "   cd $DEPLOYMENT_DIR"
echo "   docker compose -f docker-compose.prod.yml restart geosearch-app"
echo ""
echo "4. View logs:"
echo "   docker compose -f docker-compose.prod.yml logs -f geosearch-app"
echo ""
echo "5. Load MeSH data:"
echo "   docker compose -f docker-compose.prod.yml exec geosearch-app python scripts/load_mesh_full.py"
echo ""
echo "6. Ingest sample data:"
echo "   docker compose -f docker-compose.prod.yml exec geosearch-app bash scripts/quick_ingest.sh"
echo ""
echo -e "${YELLOW}Database Credentials:${NC}"
POSTGRES_PASS=$(ssh root@$VM_IP "grep POSTGRES_PASSWORD $DEPLOYMENT_DIR/.env | cut -d= -f2")
echo "  User: geouser"
echo "  Password: $POSTGRES_PASS"
echo ""
echo -e "${YELLOW}Backup your .env file!${NC}"
echo "  scp root@$VM_IP:$DEPLOYMENT_DIR/.env ./backup_geosearch.env"
echo ""
