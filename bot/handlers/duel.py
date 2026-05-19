"""
Stage B: «дуэль» — сравнение двух уже оценённых квартир по факторам.

Команда /duel выбирает 2 случайные квартиры этого профиля, у которых:
- есть запись в ratings (то есть пользователь их уже видел в /next и хотя бы что-то проставил)
- есть фото и хотя бы один PhotoEmbedding
=> для них уже посчитан travel_min (flat_poi_travel), 2GIS лишний раз не дёргается.

Показывает две карточки и три раунда A/B: beauty → price_quality → distance_pref.
Записи копятся в pairwise_ratings (upsert по uix_pairwise).
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from aiogram import Bot, Router, types, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
)
from sqlalchemy import select, desc, func, exists
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import selectinload

from core.session import async_session
from models import (
    User,
    Profile,
    Rating,
    Flat,
    FlatPhoto,
    PhotoEmbedding,
    POI,
    ProfilePOI,
    FlatPoiTravel,
    PairwiseRating,
)
from bot.handlers.browsing import (
    build_flat_caption,
    build_media_for_photos,
    _send_flat_media_group,
    _throttle_before_next_flat,
)

router = Router()

FACTOR_ORDER: List[Tuple[str, str, str]] = [
    ("beauty", "b", "Что красивее (Beauty)?"),
    ("price_quality", "p", "Что лучше по цене/качеству (Price)?"),
    ("distance_pref", "d", "Что лучше по расположению (Location)?"),
]
FACTOR_BY_SHORT = {short: (full, label) for full, short, label in FACTOR_ORDER}

MAX_PAIR_ATTEMPTS = 12

# Сентинел из mock-провайдера / неуспешного запроса; всё что >= этого — «нет реального времени».
TRAVEL_SENTINEL_MIN = 999


def _has_valid_travel_subq(profile_id: int):
    """Существует запись flat_poi_travel с реальным travel_min по одной из POI этого профиля."""
    return (
        select(1)
        .select_from(FlatPoiTravel)
        .join(
            ProfilePOI,
            (ProfilePOI.poi_id == FlatPoiTravel.poi_id)
            & (ProfilePOI.mode == FlatPoiTravel.mode),
        )
        .where(
            FlatPoiTravel.flat_id == Flat.id,
            FlatPoiTravel.travel_min.isnot(None),
            FlatPoiTravel.travel_min < TRAVEL_SENTINEL_MIN,
            ProfilePOI.profile_id == profile_id,
        )
    )


def _factor_kb(a_id: int, b_id: int, factor_short: str) -> InlineKeyboardMarkup:
    base = f"dl:{a_id}:{b_id}:{factor_short}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🅰 A лучше", callback_data=f"{base}:a"),
                InlineKeyboardButton(text="🅱 B лучше", callback_data=f"{base}:b"),
            ],
            [
                InlineKeyboardButton(text="≈ Одинаково / пропустить", callback_data=f"{base}:s"),
            ],
        ]
    )


def _next_round_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🥊 Ещё дуэль", callback_data="dlnext")],
        ]
    )


async def _active_user_profile(tg_user_id: int) -> Optional[Tuple[User, Profile]]:
    async with async_session() as session:
        user_res = await session.execute(select(User).where(User.tg_user_id == tg_user_id))
        user = user_res.scalar_one_or_none()
        if not user:
            return None
        profile_res = await session.execute(
            select(Profile).where(Profile.user_id == user.id).order_by(desc(Profile.created_at)).limit(1)
        )
        profile = profile_res.scalar_one_or_none()
        if not profile:
            return None
        return user, profile


async def _count_rated_flats_with_embeddings(session, profile_id: int) -> int:
    has_photos_subq = select(1).where(
        (FlatPhoto.flat_id == Flat.id)
        & exists(select(1).where(PhotoEmbedding.photo_id == FlatPhoto.id))
    )
    rated_subq = select(1).where(
        (Rating.profile_id == profile_id)
        & (Rating.flat_id == Flat.id)
        & (Rating.skipped.is_(False))
        & (
            (Rating.beauty.isnot(None))
            | (Rating.price_quality.isnot(None))
            | (Rating.distance_pref.isnot(None))
        )
    )
    q = (
        select(func.count())
        .select_from(Flat)
        .where(exists(rated_subq))
        .where(exists(has_photos_subq))
        .where(exists(_has_valid_travel_subq(profile_id)))
    )
    return int(await session.scalar(q) or 0)


async def _pair_already_full(session, *, profile_id: int, user_id: int, a_id: int, b_id: int) -> bool:
    """Все 3 фактора уже размечены для этой канонической пары."""
    q = select(func.count()).select_from(PairwiseRating).where(
        PairwiseRating.profile_id == profile_id,
        PairwiseRating.user_id == user_id,
        PairwiseRating.flat_a_id == a_id,
        PairwiseRating.flat_b_id == b_id,
    )
    n = int(await session.scalar(q) or 0)
    return n >= 3


async def _pick_pair(session, *, profile_id: int, user_id: int) -> Optional[Tuple[Flat, Flat]]:
    """Случайные 2 квартиры из уже оценённых пользователем в этом профиле, с фото+эмбеддингом."""
    has_photos_subq = select(1).where(
        (FlatPhoto.flat_id == Flat.id)
        & exists(select(1).where(PhotoEmbedding.photo_id == FlatPhoto.id))
    )
    rated_subq = select(1).where(
        (Rating.profile_id == profile_id)
        & (Rating.flat_id == Flat.id)
        & (Rating.skipped.is_(False))
        & (
            (Rating.beauty.isnot(None))
            | (Rating.price_quality.isnot(None))
            | (Rating.distance_pref.isnot(None))
        )
    )
    base_q = (
        select(Flat)
        .options(selectinload(Flat.photos).selectinload(FlatPhoto.embedding))
        .where(exists(rated_subq))
        .where(exists(has_photos_subq))
        .where(exists(_has_valid_travel_subq(profile_id)))
    )

    for _ in range(MAX_PAIR_ATTEMPTS):
        rows = await session.execute(base_q.order_by(func.random()).limit(2))
        flats = rows.scalars().all()
        if len(flats) < 2:
            return None
        a, b = flats[0], flats[1]
        if a.id == b.id:
            continue
        x, y = (a, b) if a.id < b.id else (b, a)
        if await _pair_already_full(
            session, profile_id=profile_id, user_id=user_id, a_id=x.id, b_id=y.id
        ):
            continue
        return x, y
    return None


async def _travel_lines_for(session, *, profile_id: int, flat_id: int) -> List[str]:
    rows = await session.execute(
        select(POI, ProfilePOI.mode, FlatPoiTravel.travel_min)
        .join(ProfilePOI, ProfilePOI.poi_id == POI.id)
        .join(
            FlatPoiTravel,
            (FlatPoiTravel.poi_id == POI.id)
            & (FlatPoiTravel.mode == ProfilePOI.mode)
            & (FlatPoiTravel.flat_id == flat_id),
        )
        .where(ProfilePOI.profile_id == profile_id)
        .order_by(ProfilePOI.priority.asc(), POI.id.asc())
    )
    out: List[str] = []
    for poi, _mode, travel_min in rows.all():
        if travel_min is None:
            continue
        out.append(f"🚌 {travel_min} мин до «{poi.label}» (ОТ)")
    return out


async def _send_one_flat(message: types.Message, flat: Flat, prefix: str, travel_lines: List[str]) -> None:
    photos = (flat.photos or [])[:10]
    caption = f"<b>{prefix}</b>\n" + build_flat_caption(flat, travel_lines)
    if not photos:
        await message.answer(caption, parse_mode="HTML")
        return
    media = await build_media_for_photos(photos, caption=caption, parse_mode="HTML")
    await _send_flat_media_group(
        message, media, flat_url=flat.url, caption=caption, parse_mode="HTML"
    )


async def _invalidate_previous_round(bot: Bot, state: FSMContext) -> None:
    """Гасит кнопки у предыдущего сообщения дуэли (раунд или «Дальше?»), если оно ещё активно."""
    data = await state.get_data()
    chat_id = data.get("duel_chat_id")
    msg_id = data.get("duel_active_msg_id")
    if not chat_id or not msg_id:
        return
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text="❌ Предыдущая дуэль отменена (запущена новая).",
            reply_markup=None,
        )
    except TelegramBadRequest:
        try:
            await bot.edit_message_reply_markup(
                chat_id=chat_id, message_id=msg_id, reply_markup=None
            )
        except TelegramBadRequest:
            pass
    await state.update_data(duel_active_msg_id=None, duel_chat_id=None)


@router.message(Command("duel", "pair", "pairwise"))
async def cmd_duel(message: types.Message, state: FSMContext, bot: Bot):
    await _invalidate_previous_round(bot, state)
    await _start_duel(message, message.from_user.id, state)


@router.callback_query(F.data == "dlnext")
async def cb_duel_next(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    # «🥊 Ещё дуэль» — текущее сообщение само станет prev, его и гасим
    await _invalidate_previous_round(bot, state)
    await _throttle_before_next_flat(callback.message)
    await _start_duel(callback.message, callback.from_user.id, state)


async def _start_duel(message: types.Message, tg_user_id: int, state: FSMContext) -> None:
    pair = await _active_user_profile(tg_user_id)
    if not pair:
        await message.answer("Сначала /start и /new_profile.")
        return
    user, profile = pair

    async with async_session() as session:
        n_pool = await _count_rated_flats_with_embeddings(session, profile.id)
        if n_pool < 2:
            await message.answer(
                "Нужно сначала оценить хотя бы 2 квартиры в этом профиле через /next."
            )
            return
        chosen = await _pick_pair(session, profile_id=profile.id, user_id=user.id)
        if not chosen:
            await message.answer(
                "Все возможные пары из оценённых квартир уже размечены. "
                "Оцените ещё квартир через /next и возвращайтесь."
            )
            return
        a, b = chosen
        tl_a = await _travel_lines_for(session, profile_id=profile.id, flat_id=a.id)
        tl_b = await _travel_lines_for(session, profile_id=profile.id, flat_id=b.id)

    await message.answer(
        f"🥊 <b>Дуэль</b>. Профиль: <b>{profile.alias}</b>. Сейчас покажу две квартиры.",
        parse_mode="HTML",
    )
    await _send_one_flat(message, a, "🅰 Квартира A", tl_a)
    await _send_one_flat(message, b, "🅱 Квартира B", tl_b)

    first_full, first_short, first_text = FACTOR_ORDER[0]
    round_msg = await message.answer(
        f"Раунд 1/3 — {first_text}",
        reply_markup=_factor_kb(a.id, b.id, first_short),
    )
    await state.update_data(
        duel_active_msg_id=round_msg.message_id,
        duel_chat_id=round_msg.chat.id,
    )


@router.callback_query(F.data.startswith("dl:"))
async def cb_duel_choice(callback: types.CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split(":")
    if len(parts) != 5:
        await callback.answer()
        return
    _, a_str, b_str, factor_short, choice = parts
    if factor_short not in FACTOR_BY_SHORT or choice not in ("a", "b", "s"):
        await callback.answer()
        return
    try:
        a_id, b_id = int(a_str), int(b_str)
    except ValueError:
        await callback.answer()
        return
    if a_id == b_id:
        await callback.answer()
        return
    if a_id > b_id:
        a_id, b_id = b_id, a_id

    data = await state.get_data()
    active_msg_id = data.get("duel_active_msg_id")
    active_chat_id = data.get("duel_chat_id")
    if (
        active_msg_id is not None
        and (
            active_msg_id != callback.message.message_id
            or (active_chat_id is not None and active_chat_id != callback.message.chat.id)
        )
    ):
        await callback.answer(
            "Эта дуэль уже неактуальна — запустите новую через /duel.",
            show_alert=True,
        )
        return

    full_factor, _ = FACTOR_BY_SHORT[factor_short]
    pair_info = await _active_user_profile(callback.from_user.id)
    if not pair_info:
        await callback.answer("Нет профиля", show_alert=True)
        return
    user, profile = pair_info

    if choice in ("a", "b"):
        preferred_id = a_id if choice == "a" else b_id
        async with async_session() as session:
            stmt = (
                insert(PairwiseRating)
                .values(
                    user_id=user.id,
                    profile_id=profile.id,
                    flat_a_id=a_id,
                    flat_b_id=b_id,
                    factor=full_factor,
                    preferred_flat_id=preferred_id,
                )
                .on_conflict_do_update(
                    constraint="uix_pairwise",
                    set_={"preferred_flat_id": preferred_id},
                )
            )
            await session.execute(stmt)
            await session.commit()

    await callback.answer()

    # Следующий раунд по тем же двум квартирам
    idx = next(i for i, (_, s, _) in enumerate(FACTOR_ORDER) if s == factor_short)
    if idx + 1 < len(FACTOR_ORDER):
        nxt_full, nxt_short, nxt_text = FACTOR_ORDER[idx + 1]
        try:
            await callback.message.edit_text(
                f"Раунд {idx + 2}/3 — {nxt_text}",
                reply_markup=_factor_kb(a_id, b_id, nxt_short),
            )
        except TelegramBadRequest:
            await callback.message.answer(
                f"Раунд {idx + 2}/3 — {nxt_text}",
                reply_markup=_factor_kb(a_id, b_id, nxt_short),
            )
        return

    try:
        await callback.message.edit_text("✅ Дуэль записана.")
    except TelegramBadRequest:
        pass
    nxt_msg = await callback.message.answer("Дальше?", reply_markup=_next_round_kb())
    await state.update_data(
        duel_active_msg_id=nxt_msg.message_id,
        duel_chat_id=nxt_msg.chat.id,
    )


@router.message(Command("duel_stats"))
async def cmd_duel_stats(message: types.Message):
    pair = await _active_user_profile(message.from_user.id)
    if not pair:
        await message.answer("Сначала /start и /new_profile.")
        return
    user, profile = pair
    async with async_session() as session:
        n_total = int(
            await session.scalar(
                select(func.count())
                .select_from(PairwiseRating)
                .where(
                    PairwiseRating.profile_id == profile.id,
                    PairwiseRating.user_id == user.id,
                )
            )
            or 0
        )
        rows = await session.execute(
            select(PairwiseRating.factor, func.count())
            .where(
                PairwiseRating.profile_id == profile.id,
                PairwiseRating.user_id == user.id,
            )
            .group_by(PairwiseRating.factor)
        )
        by_factor = {f: int(c) for f, c in rows.all()}
        pool = await _count_rated_flats_with_embeddings(session, profile.id)
    lines = [
        f"📊 Дуэли по профилю <b>{profile.alias}</b>: всего записей <b>{n_total}</b>",
        f"  • beauty: {by_factor.get('beauty', 0)}",
        f"  • price_quality: {by_factor.get('price_quality', 0)}",
        f"  • distance_pref: {by_factor.get('distance_pref', 0)}",
        "",
        f"Пул квартир, доступных для пары: <b>{pool}</b> (оценённые + с эмбеддингами фото).",
        "Команды: /duel — новая пара.",
    ]
    await message.answer("\n".join(lines), parse_mode="HTML")
