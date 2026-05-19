import os
from typing import List
from dotenv import load_dotenv

load_dotenv()


def _env_bool(key: str, default: bool = True) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


class Settings:
    # Database
    POSTGRES_USER = os.environ.get("POSTGRES_USER", "postgres")
    POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "postgres")
    POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "localhost")
    POSTGRES_PORT = os.environ.get("POSTGRES_PORT", "5432")
    POSTGRES_DB = os.environ.get("POSTGRES_DB", "smartrent")
    
    database_uri = os.environ.get(
        "DATABASE_URL", 
        f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
    )
    
    # Read-Only Replicas (comma separated)
    ro_database_uris: List[str] = [
        uri.strip() 
        for uri in os.environ.get("RO_DATABASE_URLS", "").split(",") 
        if uri.strip()
    ]

    # Redis
    REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    # S3 / MinIO
    S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "http://localhost:9000")
    S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "minioadmin")
    S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY", "minioadmin")
    S3_BUCKET = os.environ.get("S3_BUCKET", "smartrent-media")

    # Telegram
    BOT_TOKEN = os.environ.get("BOT_TOKEN")

    # Бот: подгружать новые объявления с Циана при нехватке квартир (/next, фон).
    # CIAN_FETCH_ENABLED=false — только flats уже в БД.
    CIAN_FETCH_ENABLED = _env_bool("CIAN_FETCH_ENABLED", True)

settings = Settings()
