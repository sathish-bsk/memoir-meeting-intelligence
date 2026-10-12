FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies
COPY core_api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install shared library
COPY videoclip_common/ /tmp/videoclip_common/
RUN pip install --no-cache-dir /tmp/videoclip_common && rm -rf /tmp/videoclip_common

# Copy application code
COPY core_api/app/ ./app/
COPY core_api/alembic/ ./alembic/
COPY core_api/alembic.ini ./alembic.ini

# Create directories
RUN mkdir -p /app/keys /app/data

ENV HOST=0.0.0.0
ENV PORT=8000
ENV LOG_LEVEL=INFO

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
