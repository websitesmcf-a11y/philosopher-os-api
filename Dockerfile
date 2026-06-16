FROM python:3.12-slim

# The repo structure has main.py at apps/api/app/main.py
# So we set WORKDIR to apps/api where the app package lives
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy everything
COPY . .

# Now cd into the api subdirectory and run
WORKDIR /app

EXPOSE 8000

CMD python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
