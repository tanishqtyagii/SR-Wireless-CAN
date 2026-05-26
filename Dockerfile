FROM node:20-alpine AS frontend-build
WORKDIR /build/frontend/web
COPY frontend/web/package*.json ./
RUN npm install --include=dev --no-fund --no-audit
COPY frontend/web/ ./
RUN npm run build

FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FLASH_SIMULATE=1 \
    VCU_HOST=0.0.0.0 \
    VCU_PORT=8000
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
COPY --from=frontend-build /build/frontend/web/dist ./frontend/web/dist
RUN mkdir -p /app/db/uploads
EXPOSE 8000
VOLUME ["/app/db"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
