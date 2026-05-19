# PARS — Telegram bot + ML pipeline image.
# Includes the full ML stack so that the same image runs both the bot
# (long-running aiogram polling) and one-off CLI scripts (training, scoring,
# evaluation, plotting).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System libraries needed by torch / Pillow / asyncpg compile-free builds.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        ca-certificates \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements-ml.txt ./
RUN pip install --upgrade pip && \
    pip install -r requirements.txt && \
    pip install -r requirements-ml.txt

COPY . .

# Default command: run the Telegram bot. Override in docker-compose for
# alembic / scripts / celery worker / one-off jobs.
CMD ["python", "-m", "bot.main"]
