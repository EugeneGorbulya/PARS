import asyncio
import logging
import re
from typing import List, Optional

import httpx
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, InputMediaPhoto
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func, exists, and_, update
from sqlalchemy.dialects.postgresql import insert

from core.session import async_session
from core.config import settings
from models import User, Profile, Rating, Flat, FlatPhoto, PhotoEmbedding, POI, ProfilePOI, FlatPoiTravel
from services.recommendation.selector import RecommendationService
from services.cian_parser.service import CianFetcherService
from services.image_downloader.service import ImageDownloaderService
from services.s3.client import S3Client
from services.geo.provider import get_geo_provider
from bot.keyboards import browsing_kb

logger = logging.getLogger("bot.media")
router = Router()


TELEGRAM_PHOTO_MAX_BYTES = 10 * 1024 * 1024  # 10 МБ — лимит sendPhoto

_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/118.0.0.0 Safari/537.36"
    ),
    "Referer": "https://cian.ru/",
}


async def _bytes_from_s3(s3: S3Client, storage_uri: str) -> Optional[bytes]:
    try:
        data = await s3.download_bytes(s3_uri=storage_uri)
        return data
    except Exception as e:
        logger.warning("photo: minio fail uri=%s err=%s", storage_uri, e)
        return None


async def _bytes_from_http(http: httpx.AsyncClient, url: str) -> Optional[bytes]:
    try:
        r = await http.get(url)
        r.raise_for_status()
        return r.content
    except Exception as e:
        logger.warning("photo: http fail url=%s err=%s", url, e)
        return None


async def _resolve_photo_bytes(
    s3: S3Client, http: httpx.AsyncClient, photo: FlatPhoto
) -> tuple[Optional[bytes], Optional[str]]:
    """
    Возвращает (bytes, source) где source = 's3' | 'http' | None.
    Сначала MinIO (по photo.embedding.storage_uri), затем фолбэк на photo.url.
    """
    embedding = getattr(photo, "embedding", None)
    storage_uri = getattr(embedding, "storage_uri", None) if embedding else None
    model = getattr(embedding, "model", None) if embedding else None
    if storage_uri and storage_uri.startswith("s3://") and model != "FAILED":
        data = await _bytes_from_s3(s3, storage_uri)
        if data:
            return data, "s3"

    data = await _bytes_from_http(http, photo.url)
    if data:
        return data, "http"

    return None, None


async def build_media_for_photos(
    photos: List[FlatPhoto], *, caption: str, parse_mode: str = "HTML"
) -> List[InputMediaPhoto]:
    """
    Берёт байты у каждой фотографии (MinIO → http://cian fallback) и собирает
    InputMediaPhoto(BufferedInputFile). Telegram сам ничего не качает, поэтому
    WEBPAGE_CURL_FAILED не возникает. На первое фото вешается caption.
    """
    if not photos:
        return []
    s3 = S3Client()
    media: List[InputMediaPhoto] = []
    async with httpx.AsyncClient(
        timeout=15.0, headers=_HTTP_HEADERS, follow_redirects=True
    ) as http:
        for photo in photos:
            data, src = await _resolve_photo_bytes(s3, http, photo)
            if not data:
                logger.warning(
                    "photo: skipped (no bytes) flat_id=%s photo_id=%s url=%s",
                    photo.flat_id, photo.id, photo.url,
                )
                continue
            if len(data) > TELEGRAM_PHOTO_MAX_BYTES:
                logger.warning(
                    "photo: skipped (>10MB) flat_id=%s photo_id=%s size=%d",
                    photo.flat_id, photo.id, len(data),
                )
                continue
            file = BufferedInputFile(data, filename=f"{photo.id}.jpg")
            if not media:
                media.append(
                    InputMediaPhoto(media=file, caption=caption, parse_mode=parse_mode)
                )
            else:
                media.append(InputMediaPhoto(media=file))
            logger.info(
                "photo: ok flat_id=%s photo_id=%s src=%s size=%d",
                photo.flat_id, photo.id, src, len(data),
            )
    return media


def build_flat_caption(flat, travel_lines: list[str] | None = None) -> str:
    """Подпись карточки квартиры. Используется и в /next, и в /duel."""
    if flat.rooms is None:
        rooms_str = "—"
    elif flat.rooms == 0:
        rooms_str = "студия"
    else:
        rooms_str = f"{flat.rooms}-комн."
    caption = (
        f"<b>{flat.address or 'Адрес не указан'}</b>\n"
        f"💰 {flat.price_rub} ₽ | 🛏 {rooms_str} | 📐 {flat.area_sqm} м² | 🏢 {flat.floor}/{flat.floors_total} эт.\n"
        f"🚇 {flat.nearest_metro or '-'}\n"
    )
    if travel_lines:
        caption += "\n" + "\n".join(travel_lines) + "\n"
    caption += f"\n<a href='{flat.url}'>🔗 Открыть на Циан</a>"
    return caption


def _failed_media_index_from_telegram_error(description: str) -> int | None:
    """В ошибке вида 'failed to send message #9' — 1-based номер элемента в sendMediaGroup."""
    m = re.search(r"message\s*#(\d+)", description, re.IGNORECASE)
    if not m:
        return None
    n = int(m.group(1))
    return n - 1 if n >= 1 else None


FLOOD_EXTRA_SECONDS = 10
MAX_FLOOD_RETRIES = 3

THROTTLE_BETWEEN_FLATS_SEC = 5


async def _throttle_before_next_flat(message: types.Message) -> None:
    """Пауза после оценки, чтобы не превысить flood-лимиты Telegram. Показывает «печатает…»."""
    try:
        await message.bot.send_chat_action(message.chat.id, "typing")
    except Exception:
        pass
    await asyncio.sleep(THROTTLE_BETWEEN_FLATS_SEC)


async def _sleep_for_flood(e: TelegramRetryAfter) -> None:
    wait_s = int(getattr(e, "retry_after", 0) or 0) + FLOOD_EXTRA_SECONDS
    logger.warning(
        "Telegram flood control: retry_after=%ss, sleeping %ss",
        getattr(e, "retry_after", "?"),
        wait_s,
    )
    await asyncio.sleep(max(wait_s, 1))


async def _answer_media_group_retrying(message: types.Message, work: list[InputMediaPhoto]) -> None:
    """Повтор при обрыве сети / flood control. retry_after + 10s по требованию Telegram."""
    delays = (0.0, 1.2, 3.0)
    last: Exception | None = None
    flood_retries = 0
    attempt = 0
    while attempt < 3:
        if delays[attempt] > 0:
            await asyncio.sleep(delays[attempt])
        try:
            await message.answer_media_group(work)
            return
        except TelegramRetryAfter as e:
            last = e
            if flood_retries >= MAX_FLOOD_RETRIES:
                logger.error("sendMediaGroup: flood control exceeded retry budget (n=%d)", flood_retries)
                raise
            flood_retries += 1
            await _sleep_for_flood(e)
            continue  # без увеличения attempt
        except TelegramNetworkError as e:
            last = e
            logger.warning(
                "sendMediaGroup: network error (attempt=%d/3, items=%d): %s",
                attempt + 1, len(work), e,
            )
            if attempt == 2:
                raise
        except asyncio.TimeoutError as e:
            last = e
            logger.warning(
                "sendMediaGroup: timeout (attempt=%d/3, items=%d)", attempt + 1, len(work)
            )
            if attempt == 2:
                raise
        attempt += 1
    assert last is not None
    raise last


async def _answer_photo_retrying(
    message: types.Message, media: str, caption: str | None, parse_mode: str | None
) -> None:
    delays = (0.0, 1.2, 3.0)
    last: Exception | None = None
    flood_retries = 0
    attempt = 0
    while attempt < 3:
        if delays[attempt] > 0:
            await asyncio.sleep(delays[attempt])
        try:
            await message.answer_photo(media, caption=caption, parse_mode=parse_mode)
            return
        except TelegramRetryAfter as e:
            last = e
            if flood_retries >= MAX_FLOOD_RETRIES:
                logger.error("sendPhoto: flood control exceeded retry budget (n=%d)", flood_retries)
                raise
            flood_retries += 1
            await _sleep_for_flood(e)
            continue
        except TelegramNetworkError as e:
            last = e
            logger.warning("sendPhoto: network error (attempt=%d/3) url=%s: %s", attempt + 1, media, e)
            if attempt == 2:
                raise
        except asyncio.TimeoutError as e:
            last = e
            logger.warning("sendPhoto: timeout (attempt=%d/3) url=%s", attempt + 1, media)
            if attempt == 2:
                raise
        attempt += 1
    assert last is not None
    raise last


def _ensure_caption_on_first(
    work: list[InputMediaPhoto], caption: str | None, parse_mode: str | None
) -> None:
    """Гарантирует, что caption висит на первом элементе после любых pop()."""
    if not work or not caption:
        return
    if any((m.caption or "") for m in work):
        return
    first = work[0]
    work[0] = InputMediaPhoto(media=first.media, caption=caption, parse_mode=parse_mode)


async def _send_flat_media_group(
    message: types.Message,
    media: list[InputMediaPhoto],
    *,
    flat_url: str,
    caption: str | None = None,
    parse_mode: str | None = "HTML",
) -> None:
    """
    Telegram скачивает URL фото у себя; при битой ссылке — WEBPAGE_CURL_FAILED.
    Убираем проблемные кадры и повторяем; одно фото — answer_photo; без фото — caption + ссылка.
    caption всегда сохраняется и переносится на первый оставшийся элемент.
    """
    if caption is None and media:
        caption = media[0].caption
        parse_mode = media[0].parse_mode or parse_mode

    if not media:
        text = (caption + "\n\n" if caption else "")
        await message.answer(
            f"{text}<i>Фото недоступны.</i> <a href='{flat_url}'>🔗 Открыть на Циан</a>",
            parse_mode=parse_mode or "HTML",
        )
        return

    work = list(media)
    _ensure_caption_on_first(work, caption, parse_mode)
    initial_urls = [m.media for m in work]
    logger.info("sendMediaGroup: start flat_url=%s items=%d", flat_url, len(work))
    last_err: str | None = None
    for _ in range(len(media) + 5):
        if len(work) >= 2:
            try:
                await _answer_media_group_retrying(message, work)
                logger.info(
                    "sendMediaGroup: ok flat_url=%s sent=%d/%d",
                    flat_url, len(work), len(initial_urls),
                )
                return
            except TelegramBadRequest as e:
                desc = (e.message or str(e)) or ""
                last_err = desc
                low = desc.lower()
                if (
                    "webpage_curl" not in low
                    and "failed to send" not in low
                    and "wrong file" not in low
                ):
                    logger.error(
                        "sendMediaGroup: non-recoverable BadRequest flat_url=%s: %s",
                        flat_url, desc,
                    )
                    raise
                idx = _failed_media_index_from_telegram_error(desc)
                if idx is not None and 0 <= idx < len(work):
                    dropped = work[idx].media
                    work.pop(idx)
                    logger.warning(
                        "sendMediaGroup: TG не смог скачать фото #%d url=%s "
                        "(%s). Осталось %d из %d.",
                        idx + 1, dropped, desc, len(work), len(initial_urls),
                    )
                else:
                    dropped = work[-1].media if work else "?"
                    work.pop()
                    logger.warning(
                        "sendMediaGroup: BadRequest без индекса (%s). "
                        "Снимаем последнее фото url=%s. Осталось %d из %d.",
                        desc, dropped, len(work), len(initial_urls),
                    )
                _ensure_caption_on_first(work, caption, parse_mode)
                continue
            except TelegramNetworkError as e:
                dropped = work[-1].media if work else "?"
                work.pop()
                logger.warning(
                    "sendMediaGroup: NetworkError, снимаем последнее фото url=%s. "
                    "Осталось %d из %d. err=%s",
                    dropped, len(work), len(initial_urls), e,
                )
                _ensure_caption_on_first(work, caption, parse_mode)
                continue

        if len(work) == 1:
            m0 = work[0]
            single_caption = m0.caption or caption
            single_pm = m0.parse_mode or parse_mode
            try:
                await _answer_photo_retrying(message, m0.media, single_caption, single_pm)
                logger.info(
                    "sendPhoto fallback: ok flat_url=%s url=%s", flat_url, m0.media
                )
                return
            except TelegramBadRequest as e:
                logger.error(
                    "sendPhoto fallback: BadRequest flat_url=%s url=%s: %s",
                    flat_url, m0.media, e,
                )
                text = (single_caption + "\n\n" if single_caption else "")
                await message.answer(
                    f"{text}<i>Фото не загрузилось в Telegram.</i> "
                    f"<a href='{flat_url}'>🔗 Открыть на Циан</a>",
                    parse_mode=single_pm or "HTML",
                )
                return
            except TelegramNetworkError as e:
                logger.error(
                    "sendPhoto fallback: NetworkError flat_url=%s url=%s: %s",
                    flat_url, m0.media, e,
                )
                text = (single_caption + "\n\n" if single_caption else "")
                await message.answer(
                    f"{text}<i>Сеть до Telegram нестабильна, фото не отправилось.</i> "
                    f"<a href='{flat_url}'>🔗 Открыть на Циан</a>",
                    parse_mode=single_pm or "HTML",
                )
                return

    logger.error(
        "sendMediaGroup: исчерпали попытки flat_url=%s last_err=%s urls=%s",
        flat_url, last_err, initial_urls,
    )
    text = (caption + "\n\n" if caption else "")
    await message.answer(
        f"{text}<i>Не удалось показать фото ({last_err or 'ошибка Telegram'}).</i> "
        f"<a href='{flat_url}'>🔗 Открыть на Циан</a>",
        parse_mode=parse_mode or "HTML",
    )


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
        
        if settings.CIAN_FETCH_ENABLED and count < min_buffer:
            asyncio.create_task(background_fetch(profile.id))

async def background_fetch(profile_id: int):
    if not settings.CIAN_FETCH_ENABLED:
        return
    async with async_session() as session:
        profile_res = await session.execute(select(Profile).where(Profile.id == profile_id))
        profile = profile_res.scalar_one_or_none()
        if not profile:
            return
            
        fetcher = CianFetcherService(session)
        try:
            filters = dict(profile.cian_filter or {})
            region_id = filters.pop("region_id", 1)
            count, _ = await fetcher.fetch_and_save(region_id=region_id, max_pages=5, max_flats=10, **filters)
        except Exception as e:
            print(f"Background fetch failed: {e}")


@router.message(Command("available"))
async def cmd_available(message: types.Message):
    """Показать, сколько квартир можно оценить без ожидания (под текущий профиль, с фото)."""
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
        count = await service.count_available_flats(user.id, profile.id)
    await message.answer(
        f"📊 Квартир доступно для оценки (без ожидания): <b>{count}</b>\n\n"
        f"Профиль: <b>{profile.alias}</b>. Чтобы смотреть следующую — /next",
        parse_mode="HTML"
    )


@router.message(Command("next"))
async def cmd_next(message: types.Message, *, _tg_user_id: int | None = None):
    """
    _tg_user_id — Telegram id пользователя, если message от бота (например после callback по оценке).
    Иначе message.from_user — бот, и User в БД не находится.
    """
    user_id = _tg_user_id if _tg_user_id is not None else message.from_user.id
    
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
            if not settings.CIAN_FETCH_ENABLED:
                await message.answer(
                    "🤷‍♂️ Нет квартир для показа (все оценены или не подходят под фильтр / нет эмбеддингов фото). "
                    "Загрузка с Циана выключена (CIAN_FETCH_ENABLED=false) — используются только объявления из базы."
                )
                return
            status_msg = await message.answer("🔍 Ищу объявления и подготавливаю данные...")
            fetcher = CianFetcherService(session)
            try:
                filters = dict(profile.cian_filter or {})
                region_id = filters.pop("region_id", 1)
                count, saved_flat_ids = await fetcher.fetch_and_save(
                    region_id=region_id, max_pages=5, max_flats=10, **filters
                )
                if count == 0:
                    await status_msg.edit_text("🤷‍♂️ Пока ничего нет.")
                    return
                # Сначала загружаем фото для этих квартир, потом показываем в чате
                async with async_session() as session2:
                    downloader = ImageDownloaderService(session2)
                    await downloader.process_photos_for_flat_ids(saved_flat_ids)
                await status_msg.edit_text(f"✅ Найдено {count} новых.")
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

        # Время в пути до точки назначения пользователя (профиля): из кэша или 2GIS
        travel_lines = []
        try:
            provider = get_geo_provider()
        except Exception:
            provider = None

        async with async_session() as session_geo:
            pois_res = await session_geo.execute(
                select(POI, ProfilePOI.mode)
                .join(ProfilePOI, POI.id == ProfilePOI.poi_id)
                .where(ProfilePOI.profile_id == profile.id)
                .order_by(ProfilePOI.priority.asc(), POI.id.asc())
            )
            profile_pois = pois_res.all()

        for poi, mode in profile_pois:
            cached = next(
                (t for t in (getattr(flat, "travel_times", None) or []) if t.poi_id == poi.id and t.mode == mode),
                None,
            )
            if cached:
                travel_lines.append(f"🚌 {cached.travel_min} мин до «{poi.label}» (ОТ)")
                continue
            if flat.lat is None or flat.lng is None or not provider:
                continue
            # Только реальный 2GIS — не показываем mock-время
            if not getattr(provider, "api_key", None):
                continue
            try:
                minutes = await provider.calculate_travel_time(
                    float(flat.lat), float(flat.lng), float(poi.lat), float(poi.lng), mode
                )
                travel_lines.append(f"🚌 {minutes} мин до «{poi.label}» (ОТ)")
                async with async_session() as session_save:
                    stmt = insert(FlatPoiTravel).values(
                        flat_id=flat.id, poi_id=poi.id, mode=mode, travel_min=minutes
                    ).on_conflict_do_update(
                        index_elements=["flat_id", "poi_id", "mode"],
                        set_={"travel_min": minutes},
                    )
                    await session_save.execute(stmt)
                    await session_save.commit()
            except Exception as e:
                print(f"Travel time error: {e}")

        caption = build_flat_caption(flat, travel_lines)
        media = await build_media_for_photos(photos, caption=caption, parse_mode="HTML")
        await _send_flat_media_group(
            message, media, flat_url=flat.url, caption=caption, parse_mode="HTML"
        )
        
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
        if not user:
            await callback.answer("Сначала нажмите /start")
            return

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
        await _throttle_before_next_flat(callback.message)
        await cmd_next(callback.message, _tg_user_id=callback.from_user.id)
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
        await _throttle_before_next_flat(callback.message)
        await cmd_next(callback.message, _tg_user_id=callback.from_user.id)
