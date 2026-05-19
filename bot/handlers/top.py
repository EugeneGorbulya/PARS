"""
/top — топ квартир профиля по скору модели.

Режимы (выбираются кнопками при вызове /top):
  new   — только не оценённые (что смотреть дальше)
  rated — только оценённые (проверка что модель выучила)
  all   — все вместе
"""
from __future__ import annotations

from typing import List, Optional

from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select, exists
from sqlalchemy.orm import selectinload

from core.session import async_session
from models import (
    Flat,
    FlatPhoto,
    FlatPoiTravel,
    POI,
    Profile,
    ProfileFlatScore,
    ProfilePOI,
    Rating,
    User,
)
from bot.handlers.browsing import build_flat_caption, build_media_for_photos, _send_flat_media_group

router = Router()

MODE_LABELS = {
    "new":   "🆕 Не оценённые",
    "rated": "✅ Оценённые",
    "all":   "🔀 Все вместе",
}


def _mode_kb(profile_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"top_mode:{profile_id}:{mode}")]
        for mode, label in MODE_LABELS.items()
    ])


def _top_nav_kb(profile_id: int, mode: str, offset: int, has_next: bool) -> InlineKeyboardMarkup:
    buttons: List[List[InlineKeyboardButton]] = []
    if has_next:
        buttons.append([
            InlineKeyboardButton(
                text="➡️ Следующая",
                callback_data=f"top_next:{profile_id}:{mode}:{offset + 1}",
            )
        ])
    buttons.append([
        InlineKeyboardButton(
            text="🔄 Сменить режим",
            callback_data=f"top_mode_pick:{profile_id}",
        ),
        InlineKeyboardButton(text="✅ К оценке (/next)", callback_data="top_go_next"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def _get_top_flat(
    session, profile_id: int, user_id: int, offset: int, mode: str
) -> Optional[tuple]:
    """Возвращает (ProfileFlatScore, Flat) с нужным offset или None."""
    rated_sq = select(1).where(
        (Rating.profile_id == profile_id)
        & (Rating.flat_id == Flat.id)
        & (Rating.user_id == user_id)
    )
    q = (
        select(ProfileFlatScore, Flat)
        .join(Flat, Flat.id == ProfileFlatScore.flat_id)
        .options(selectinload(Flat.photos).selectinload(FlatPhoto.embedding))
        .where(ProfileFlatScore.profile_id == profile_id)
        .order_by(ProfileFlatScore.score.desc())
        .offset(offset)
        .limit(1)
    )
    if mode == "new":
        q = q.where(~exists(rated_sq))
    elif mode == "rated":
        q = q.where(exists(rated_sq))
    # mode == "all" — без фильтра

    row = (await session.execute(q)).first()
    return row  # (ProfileFlatScore, Flat) or None


async def _travel_lines(session, profile_id: int, flat_id: int) -> List[str]:
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
    lines = []
    for poi, mode, travel_min in rows.all():
        if travel_min is not None and travel_min < 999:
            lines.append(f"🚌 {travel_min} мин до «{poi.label}»")
    return lines


async def _send_top_card(
    message: types.Message, profile_id: int, user_id: int, offset: int, mode: str
) -> None:
    async with async_session() as session:
        row = await _get_top_flat(session, profile_id, user_id, offset, mode)
        if not row:
            mode_label = MODE_LABELS.get(mode, mode)
            await message.answer(
                f"🏁 В режиме «{mode_label}» больше квартир нет.\n"
                "Выберите другой режим или запустите /next."
            )
            return

        pfs, flat = row
        next_row = await _get_top_flat(session, profile_id, user_id, offset + 1, mode)
        has_next = next_row is not None
        travel = await _travel_lines(session, profile_id, flat.id)

    mode_label = MODE_LABELS.get(mode, mode)
    rated_mark = ""
    # Для режима «все» полезно знать, оценена ли квартира
    if mode == "all":
        async with async_session() as session:
            is_rated = (await session.execute(
                select(Rating).where(
                    Rating.profile_id == profile_id,
                    Rating.flat_id == flat.id,
                    Rating.user_id == user_id,
                ).limit(1)
            )).first()
        rated_mark = " ✅" if is_rated else " 🆕"

    score_line = (
        f"🏆 <b>Скор: {float(pfs.score):.2f}</b>{rated_mark}  "
        f"(💄 {float(pfs.beauty_hat or 0):.2f} / "
        f"💰 {float(pfs.price_quality_hat or 0):.2f} / "
        f"🚌 {float(pfs.distance_hat or 0):.2f})"
    )
    caption = score_line + "\n" + build_flat_caption(flat, travel)
    photos = (flat.photos or [])[:10]
    kb = _top_nav_kb(profile_id, mode, offset, has_next)

    if not photos:
        await message.answer(caption, parse_mode="HTML", reply_markup=kb)
        return

    media = await build_media_for_photos(photos, caption=caption, parse_mode="HTML")
    await _send_flat_media_group(message, media, flat_url=flat.url, caption=caption, parse_mode="HTML")
    await message.answer(
        f"📍 {mode_label} · позиция #{offset + 1}",
        reply_markup=kb,
    )


async def _check_profile_scores(message: types.Message, tg_id: int):
    """Возвращает (user, profile) или отвечает с ошибкой и возвращает (None, None)."""
    async with async_session() as session:
        user = (await session.execute(
            select(User).where(User.tg_user_id == tg_id)
        )).scalar_one_or_none()
        if not user:
            await message.answer("Сначала /start")
            return None, None

        profile = (await session.execute(
            select(Profile).where(Profile.user_id == user.id).order_by(Profile.id.desc()).limit(1)
        )).scalar_one_or_none()
        if not profile:
            await message.answer("Нет профиля. Создайте через /new_profile.")
            return None, None

        has_scores = (await session.execute(
            select(ProfileFlatScore).where(ProfileFlatScore.profile_id == profile.id).limit(1)
        )).first()
        if not has_scores:
            await message.answer(
                "⚠️ Модель ещё не обучена или скоры не посчитаны.\n"
                "Запустите:\n"
                f"<code>python3 -m scripts.ml.train_preference_stage_a --profile-id {profile.id} --epochs 80</code>\n"
                "затем:\n"
                f"<code>python3 -m scripts.ml.score_profile_flats --profile-id {profile.id}</code>",
                parse_mode="HTML",
            )
            return None, None

    return user, profile


@router.message(Command("top"))
async def cmd_top(message: types.Message) -> None:
    user, profile = await _check_profile_scores(message, message.from_user.id)
    if not user:
        return
    await message.answer(
        "Какой топ показать?",
        reply_markup=_mode_kb(profile.id),
    )


@router.callback_query(F.data.startswith("top_mode:"))
async def cb_top_mode(callback: types.CallbackQuery) -> None:
    await callback.answer()
    _, profile_id_str, mode = callback.data.split(":")
    profile_id = int(profile_id_str)
    user, _ = await _check_profile_scores(callback.message, callback.from_user.id)
    if not user:
        return
    await _send_top_card(callback.message, profile_id, user.id, offset=0, mode=mode)


@router.callback_query(F.data.startswith("top_mode_pick:"))
async def cb_top_mode_pick(callback: types.CallbackQuery) -> None:
    await callback.answer()
    _, profile_id_str = callback.data.split(":")
    profile_id = int(profile_id_str)
    await callback.message.answer(
        "Какой топ показать?",
        reply_markup=_mode_kb(profile_id),
    )


@router.callback_query(F.data.startswith("top_next:"))
async def cb_top_next(callback: types.CallbackQuery) -> None:
    await callback.answer()
    parts = callback.data.split(":")
    profile_id = int(parts[1])
    mode = parts[2]
    offset = int(parts[3])

    async with async_session() as session:
        user = (await session.execute(
            select(User).where(User.tg_user_id == callback.from_user.id)
        )).scalar_one_or_none()
    if not user:
        await callback.message.answer("Сначала /start")
        return

    await _send_top_card(callback.message, profile_id, user.id, offset, mode)


@router.callback_query(F.data == "top_go_next")
async def cb_top_go_next(callback: types.CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer("Нажмите /next для следующей квартиры.")
