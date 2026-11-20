FROM node:24-alpine AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 APS_AUTO_SCHEMA=0 APS_EMBED_WORKER=0
COPY requirements*.txt ./
RUN pip install --no-cache-dir -r requirements-product.txt
COPY aps/ aps/
COPY migrations/ migrations/
COPY alembic.ini .
COPY --from=frontend /build/dist frontend/dist
RUN useradd --system --uid 10001 aps && mkdir .var && chown aps .var
USER aps
