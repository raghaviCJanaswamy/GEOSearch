#!/bin/bash
# Simple database initialization script
# Can be called directly from docker-compose as a fallback

set -e

echo "================================"
echo "GEOSearch Database Initialization"
echo "================================"
echo ""

# Run Python initialization script
echo "Running Python initialization..."
python3 scripts/init_database.py

if [ $? -eq 0 ]; then
    echo ""
    echo "✓ Database initialization successful!"
    exit 0
else
    echo ""
    echo "✗ Database initialization failed!"
    exit 1
fi
