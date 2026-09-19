FROM python:3.12-slim

# zoneinfo (used for Europe/Moscow) needs the system tz database
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py config.py database.py report.py ./
COPY *.xlsx ./

ENV PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data

VOLUME ["/app/data"]

CMD ["python", "bot.py"]
