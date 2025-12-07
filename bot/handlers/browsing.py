import asyncio
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.types import InputMediaPhoto
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func, exists, and_, update
from sqlalchemy.dialects.postgresql import insert

from core.session import async_session
from models import User, Profile, Rating, Flat, FlatPhoto, PhotoEmbedding
from services.recommendation.selector import RecommendationService
from services.cian_parser.service import CianFetcherService
from bot.keyboards import browsing_kb

router = Router()

async def check_and_prefetch(user_id: int, profile_id: int, min_buffer: int = 5):
    async with async_session() as session:
        rated_subquery = select(1).where(
            (Rating.user_id == user_id) & 
            (Rating.profile_id == profile_id) & 
            (Rating.flat_id == Flat.id)
        )
        query = (
            select(func.count())
            .select_from(Flat)
            .where(~exists(rated_subquery))
        )
        profile_res = await session.execute(select(Profile).where(Profile.id == profile_id))
        profile = profile_res.scalar_one_or_none()
        if not profile:
            return

        filters = profile.cian_filter
        min_p = filters.get("min_price", 0)
        max_p = filters.get("max_price", 10000000)
        rooms = filters.get("rooms", [])
        
        query = query.where(Flat.price_rub >= min_p).where(Flat.price_rub <= max_p)
        if rooms:
             if 0 in rooms:
                query = query.where(Flat.rooms.in_(rooms))
             else:
                query = query.where(Flat.rooms.in_(rooms))

        result = await session.execute(query)
        count = result.scalar()
        
        if count < min_buffer:
            asyncio.create_task(background_fetch(profile.id))

async def background_fetch(profile_id: int):
    async with async_session() as session:
        profile_res = await session.execute(select(Profile).where(Profile.id == profile_id))
        profile = profile_res.scalar_one_or_none()
        if not profile:
            return
            
        fetcher = CianFetcherService(session)
        try:
            filters = profile.cian_filter
            await fetcher.fetch_and_save(
                region_id=filters.get("region_id", 1),
                min_price=filters.get("min_price"),
                max_price=filters.get("max_price"),
                rooms=filters.get("rooms"),
                area_min=filters.get("area_min"),
                floor_pref=filters.get("floor_pref"),
                renovation=filters.get("renovation"),
                max_pages=1
            )
        except Exception as e:
            print(f"Background fetch failed: {e}")


@router.message(Command("next"))
async def cmd_next(message: types.Message):
    user_id = message.from_user.id
    
    async with async_session() as session:
        user_res = await session.execute(select(User).where(User.tg_user_id == user_id))
        user = user_res.scalar_one_or_none()
        if not user:
            await message.answer("Сначала нажмите /start")
            return

        profile_res = await session.execute(
            select(Profile).where(Profile.user_id == user.id).order_by(desc(Profile.created_at)).limit(1)
        )
        profile = profile_res.scalar_one_or_none()
        if not profile:
            await message.answer("У вас нет профиля. Создайте через /new_profile")
            return

        service = RecommendationService(session)
        flat = await service.get_next_flat(user.id, profile.id)

        await check_and_prefetch(user.id, profile.id)

        if not flat:
            status_msg = await message.answer("🔍 Квартиры закончились. Ищу свежие объявления...")
            fetcher = CianFetcherService(session)
            try:
                filters = profile.cian_filter
                count = await fetcher.fetch_and_save(
                    region_id=filters.get("region_id", 1),
                    min_price=filters.get("min_price"),
                    max_price=filters.get("max_price"),
                    rooms=filters.get("rooms"),
                    area_min=filters.get("area_min"),
                    floor_pref=filters.get("floor_pref"),
                    renovation=filters.get("renovation"),
                    max_pages=1
                )
                await status_msg.edit_text(f"✅ Найдено {count} новых. Загружаю...")
                flat = await service.get_next_flat(user.id, profile.id)
                if not flat:
                    await message.answer("🤷‍♂️ Пока ничего нет.")
                    return
            except Exception as e:
                print(f"Error fetching: {e}")
                await status_msg.edit_text("⚠️ Ошибка поиска.")
                return

        photos = flat.photos[:10] 
        if not photos:
            await message.answer("Ошибка: у квартиры нет фото.")
            return

        media = []
        caption = (
            f"<b>{flat.address or 'Адрес не указан'}</b>\n"
            f"💰 {flat.price_rub} ₽ | 📐 {flat.area_sqm} м² | 🏢 {flat.floor}/{flat.floors_total} эт.\n"
            f"🚇 {flat.nearest_metro or '-'}\n"
        )
        
        if hasattr(flat, 'travel_times') and flat.travel_times:
            travel_info = "\n".join([f"🚗 {t.travel_min} мин ({t.mode})" for t in flat.travel_times])
            caption += f"\n{travel_info}\n"
        
        caption += f"\n<a href='{flat.url}'>🔗 Открыть на Циан</a>"

        for i, photo in enumerate(photos):
            if i == 0:
                media.append(InputMediaPhoto(media=photo.url, caption=caption, parse_mode="HTML"))
            else:
                media.append(InputMediaPhoto(media=photo.url))

        await message.answer_media_group(media)
        
        # Start with BEAUTY rating
        await message.answer(
            f"Шаг 1/3: Как вам <b>Визуал / Ремонт</b>? (Beauty)",
            reply_markup=browsing_kb.get_rate_kb(flat.id, metric="beauty")
        )

@router.callback_query(F.data.startswith("rate:"))
async def process_rate(callback: types.CallbackQuery):
    # rate:score:flat_id:metric
    parts = callback.data.split(":")
    score_str = parts[1]
    flat_id = int(parts[2])
    metric = parts[3] if len(parts) > 3 else "beauty"
    
    user_id = callback.from_user.id
    skipped = (score_str == "skip")
    score = None if skipped else int(score_str)

    # Determine next step and DB field
    next_metric = None
    db_field = None
    next_text = None

    if skipped:
        # If skipped, we stop immediately
        next_metric = None
    elif metric == "beauty":
        next_metric = "price"
        db_field = "beauty"
        next_text = "Шаг 2/3: Как соотношение <b>Цена / Качество</b>? (Price)"
    elif metric == "price":
        next_metric = "dist"
        db_field = "price_quality"
        next_text = "Шаг 3/3: Как <b>Расположение</b>? (Location)"
    elif metric == "dist":
        next_metric = None # Finish
        db_field = "distance_pref"
    
    async with async_session() as session:
        user_res = await session.execute(select(User).where(User.tg_user_id == user_id))
        user = user_res.scalar_one_or_none()
        
        profile_res = await session.execute(
            select(Profile).where(Profile.user_id == user.id).order_by(desc(Profile.created_at)).limit(1)
        )
        profile = profile_res.scalar_one_or_none()

        if not profile:
            await callback.answer("Профиль не найден")
            return

        # Upsert Rating
        # We try to insert, if conflict (on unique constraint) -> update the specific field
        stmt = insert(Rating).values(
            user_id=user.id,
            profile_id=profile.id,
            flat_id=flat_id,
            skipped=skipped,
            source='telegram',
            **({db_field: score} if db_field else {})
        )
        
        stmt = stmt.on_conflict_do_update(
            constraint='uix_rating_user_profile_flat',
            set_={
                db_field: score,
                'skipped': skipped
            } if db_field else {'skipped': skipped}
        )
        
        await session.execute(stmt)
        await session.commit()

    await callback.answer()

    if skipped:
        try:
            await callback.message.edit_text("➡️ Пропущено")
        except:
            pass
        await cmd_next(callback.message)
        return

    if next_metric:
        # Update text and keyboard for next step
        try:
            await callback.message.edit_text(
                next_text,
                reply_markup=browsing_kb.get_rate_kb(flat_id, metric=next_metric),
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"Edit error: {e}")
    else:
        # Final step done
        try:
            await callback.message.edit_text("✅ Оценка сохранена!")
        except:
            pass
        # Show next flat
        await cmd_next(callback.message)
