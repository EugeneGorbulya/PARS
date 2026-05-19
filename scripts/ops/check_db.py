"""
Проверка содержимого БД: пользователи, профили (в т.ч. cian_filter), квартиры.
Запуск из корня: python3 scripts/ops/check_db.py
"""
import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select, func
from core.session import async_session
from models import User, Profile, Flat


async def main():
    async with async_session() as session:
        # Счётчики
        (users_count,) = (await session.execute(select(func.count(User.id)))).one()
        (profiles_count,) = (await session.execute(select(func.count(Profile.id)))).one()
        (flats_count,) = (await session.execute(select(func.count(Flat.id)))).one()

        print("=== Сводка ===\n")
        print(f"  users:    {users_count}")
        print(f"  profiles: {profiles_count}")
        print(f"  flats:    {flats_count}\n")

        # Пользователи
        result = await session.execute(select(User).order_by(User.id))
        users = result.scalars().all()
        if users:
            print("=== Users ===\n")
            for u in users:
                print(f"  id={u.id}  tg_user_id={u.tg_user_id}  username={u.username or '—'}")
            print()

        # Профили с cian_filter
        result = await session.execute(
            select(Profile).order_by(Profile.id)
        )
        profiles = result.scalars().all()
        if profiles:
            print("=== Profiles (cian_filter) ===\n")
            for p in profiles:
                cf = p.cian_filter or {}
                keys = list(cf.keys())
                print(f"  id={p.id}  user_id={p.user_id}  alias={p.alias!r}  city={p.city!r}")
                print(f"    cian_filter keys ({len(keys)}): {sorted(keys)}")
                print(f"    cian_filter: {json.dumps(cf, ensure_ascii=False)}")
                print()
        else:
            print("  Профилей нет.\n")

        # Несколько квартир для проверки
        if flats_count:
            result = await session.execute(
                select(Flat).order_by(Flat.id.desc()).limit(5)
            )
            last_flats = result.scalars().all()
            print("=== Последние 5 flats ===\n")
            for f in last_flats:
                print(f"  id={f.id}  cian_id={f.cian_id}  city={f.city}  price={f.price_rub}  url={f.url[:50]}...")
            print()


if __name__ == "__main__":
    asyncio.run(main())
