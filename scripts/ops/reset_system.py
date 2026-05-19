import asyncio
import sys
import os

# Add project root to path
sys.path.append(os.getcwd())

from sqlalchemy import text
from core.session import async_session
from core.config import settings
import aioboto3

async def clear_db():
    print("🧹 Очистка базы данных...")
    async with async_session() as session:
        # Список таблиц для очистки. 
        # Используем CASCADE, чтобы удалить зависимые записи.
        # alembic_version НЕ трогаем, чтобы сохранить структуру миграций.
        tables = [
            "photo_clip_embeddings",
            "photo_embeddings",
            "flat_photos", 
            "flat_poi_travel",
            "profile_flat_score",
            "pairwise_ratings",
            "profile_metrics",
            "ratings",
            "flats",
            "profiles",
            "users",
            "pois",
            "model_snapshots",
        ]
        
        tables_str = ", ".join(tables)
        try:
            # Проверяем существование таблиц перед очисткой, чтобы не падать если БД пустая
            # Но проще просто запустить TRUNCATE IF EXISTS не работает в старых версиях, 
            # но TRUNCATE работает только если таблицы есть.
            # Предполагаем, что миграции применены.
            
            await session.execute(text(f"TRUNCATE TABLE {tables_str} RESTART IDENTITY CASCADE;"))
            await session.commit()
            print("✅ База данных очищена (сброшены данные и счетчики ID).")
        except Exception as e:
            print(f"❌ Ошибка БД (возможно таблицы еще не созданы): {e}")
            await session.rollback()

async def clear_s3():
    print(f"🧹 Очистка S3 бакета: {settings.S3_BUCKET}...")
    session = aioboto3.Session()
    async with session.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
    ) as s3:
        try:
            # Проверяем, существует ли бакет
            try:
                await s3.head_bucket(Bucket=settings.S3_BUCKET)
            except:
                print(f"⚠️ Бакет {settings.S3_BUCKET} не найден.")
                return

            # Удаляем объекты
            paginator = s3.get_paginator('list_objects_v2')
            async for page in paginator.paginate(Bucket=settings.S3_BUCKET):
                if 'Contents' in page:
                    objects = [{'Key': obj['Key']} for obj in page['Contents']]
                    if objects:
                        await s3.delete_objects(
                            Bucket=settings.S3_BUCKET,
                            Delete={'Objects': objects}
                        )
                        print(f"   🗑 Удалено {len(objects)} объектов...")
            print("✅ S3 бакет очищен.")
        except Exception as e:
            print(f"❌ Ошибка S3: {e}")

async def main():
    # print("=== ЗАПУСК ПОЛНОЙ ОЧИСТКИ СИСТЕМЫ ===")
    # await clear_db()
    # await clear_s3()
    # print("=== ГОТОВО ===")
    pass

if __name__ == "__main__":
    asyncio.run(main())

