FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

# Remove .env files that might have empty vars
RUN rm -f .env .env.local 2>/dev/null; exit 0

# Ensure no empty ENV values cause Docker build failures
ENV SMTP_HOST=""
ENV SMTP_USER=""
ENV SMTP_PASSWORD=""

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
