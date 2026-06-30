FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot ./bot

# данные (БД, бэкапы, логи) — на volume
VOLUME ["/app/data"]
ENV DATABASE_URL=sqlite:////app/data/bot.db

CMD ["python", "-m", "bot.main"]
