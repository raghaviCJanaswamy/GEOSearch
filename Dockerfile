# python:3.12-slim-bookworm has fewer CVEs than 3.11-slim (Debian Bookworm, fully patched)
FROM python:3.12-slim-bookworm

WORKDIR /app

# Install system dependencies; upgrade base packages first to pull in security patches
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# --- Dependency layer (cached unless requirements.txt changes) ---
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# --- Pre-download embedding model so it is baked into the image ---
# This avoids a ~90MB runtime download on every cold start.
RUN python -c "\
from sentence_transformers import SentenceTransformer; \
SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

# --- Application code (changes frequently — kept last to preserve cache above) ---
COPY . .

# Run as non-root user to reduce attack surface
RUN useradd -m -u 1000 appuser && \
    mkdir -p logs && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 8501 8080

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=5 \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
