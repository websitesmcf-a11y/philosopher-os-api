FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Clean up .env files
RUN rm -f .env .env.local 2>/dev/null; exit 0

EXPOSE 8000

# Use exec form for better signal handling
CMD ["sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
