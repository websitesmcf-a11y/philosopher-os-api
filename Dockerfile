FROM python:3.12-slim

# Build timestamp forces cache invalidation: 2026-06-24T17:00
ARG BUILD_TIMESTAMP=2026-06-24T17:00

# Install Chromium + dependencies for cloud browser automation
# playwright-deps includes system libs; chromium-browser provides the binary
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt playwright

# Cache Chromium for Playwright (ensures pw knows where the binary is)
ENV PLAYWRIGHT_BROWSERS_PATH=/usr/bin
RUN python -m playwright install chromium 2>/dev/null || true

# Copy everything (cache-bust: 2026-06-24)
COPY . .

WORKDIR /app

EXPOSE 8000

CMD python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
