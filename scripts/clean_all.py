import asyncio
import os
import sys
from sqlalchemy import text
import aioboto3

# Add project root to sys.path
sys.path.append(os.getcwd())

from core.session import async_engine
from core.config import settings

async def clean_s3():
    print(f"🗑  Очистка S3 бакета: {settings.S3_BUCKET}...")
    session = aioboto3.Session()
    try:
        async with session.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
        ) as s3:
            # Check if bucket exists
            try:
                await s3.head_bucket(Bucket=settings.S3_BUCKET)
            except Exception:
                print("⚠️  Бакет не найден или недоступен.")
                return

            # List and delete objects
            paginator = s3.get_paginator('list_objects_v2')
            count = 0
            async for page in paginator.paginate(Bucket=settings.S3_BUCKET):
                if 'Contents' in page:
                    objects = [{'Key': obj['Key']} for obj in page['Contents']]
                    if objects:
                        await s3.delete_objects(
                            Bucket=settings.S3_BUCKET,
                            Delete={'Objects': objects}
                        )
                        count += len(objects)
                        print(f"   Удалено объектов: {len(objects)}")
            
            if count == 0:
                print("   Бакет пуст.")
            else:
                print(f"✅ S3 очищен (всего удалено: {count}).")

    except Exception as e:
        print(f"❌ Ошибка очистки S3: {e}")

async def clean_db():
    print("🗑  Очистка Базы Данных (DROP SCHEMA public)...")
    try:
        async with async_engine.begin() as conn:
            # Удаляем схему public и создаем заново
            await conn.execute(text("DROP SCHEMA public CASCADE;"))
            await conn.execute(text("CREATE SCHEMA public;"))
            # Возвращаем права (стандартные для PG)
            await conn.execute(text("GRANT ALL ON SCHEMA public TO public;"))
            await conn.execute(text(f"GRANT ALL ON SCHEMA public TO {settings.POSTGRES_USER};"))
            
        print("✅ БД полностью очищена.")
    except Exception as e:
        print(f"❌ Ошибка очистки БД: {e}")

def run_migrations():
    print("🔄 Накатываем миграции (alembic upgrade head)...")
    # Используем тот же python интерпретатор
    cmd = f"{sys.executable} -m alembic upgrade head"
    res = os.system(cmd)
    if res != 0:
        print("❌ Ошибка миграций.")
    else:
        print("✅ Миграции успешно применены.")

async def main():
    await clean_s3()
    await clean_db()
    run_migrations()

if __name__ == "__main__":
    asyncio.run(main())

