FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends sqlite3 cron && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create data and backup directories
RUN mkdir -p /app/data /app/backups

# Keep copies of data defaults OUTSIDE /app/data so the volume mount can't hide them
RUN cp /app/data/config.json /app/config_default.json 2>/dev/null || true && \
    cp "/app/data/Whitewood Family Birthdays.xlsx" "/app/birthdays_default.xlsx" 2>/dev/null || true

# Daily backup: 3am — uses SQLite's .backup command (safe on live DB)
RUN echo "0 3 * * * sqlite3 /app/data/family.db \".backup /app/backups/family-\$(date +\\%F).sqlite\" && find /app/backups -name 'family-*.sqlite' -mtime +14 -delete >> /var/log/cron.log 2>&1" | crontab -

EXPOSE 3000

CMD ["sh", "-c", "cron && gunicorn --bind 0.0.0.0:3000 --workers 1 --timeout 120 app:app"]
