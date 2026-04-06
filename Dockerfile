FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create data directory for SQLite DB and config
RUN mkdir -p /app/data

EXPOSE 8282

CMD ["gunicorn", "--bind", "0.0.0.0:8282", "--workers", "1", "--timeout", "120", "app:app"]
