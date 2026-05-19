"""
Точки назначения (POI): несколько точек на профиль, max_travel_min, priority, mode.
При /next показывается время в пути (2GIS для masstransit).
"""
import html
from typing import Optional, Tuple

from aiogram import Router, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, desc, delete, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from bot.keyboards.poi_kb import kb_add_mode, kb_poi_actions_rows, kb_poi_mode_choice
from bot.states import PoiStates
from core.session import async_session
from models import User, Profile, POI, ProfilePOI

router = Router()

DESTINATION_LABEL = "Пункт назначения"

_MODE_NAMES = {"masstransit": "ОТ", "pedestrian": "пешком"}


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


async def _profile_poi_for_user(
    session, *, profile_id: int, user_id: int, poi_id: int
) -> Optional[Tuple[POI, ProfilePOI]]:
    row = await session.execute(
        select(POI, ProfilePOI)
        .join(ProfilePOI, ProfilePOI.poi_id == POI.id)
        .where(
            ProfilePOI.profile_id == profile_id,
            ProfilePOI.poi_id == poi_id,
            POI.user_id == user_id,
        )
    )
    return row.one_or_none()


async def _next_default_priority(session, profile_id: int) -> int:
    m = await session.scalar(
        select(func.coalesce(func.max(ProfilePOI.priority), 0)).where(ProfilePOI.profile_id == profile_id)
    )
    return int(m or 0) + 1


def _mode_title(mode: str) -> str:
    return _MODE_NAMES.get(mode, mode)


@router.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    cur = await state.get_state()
    if cur is None:
        await message.answer("Нечего отменять.")
        return
    await state.clear()
    await message.answer("Ок, шаг отменён. Можно снова /poi_add или /pois.")


@router.message(Command("pois", "my_pois"))
async def cmd_pois(message: types.Message):
    tg_user_id = message.from_user.id
    pair = await _active_user_profile(tg_user_id)
    if not pair:
        await message.answer("Сначала /start и профиль /new_profile.")
        return
    user, profile = pair

    async with async_session() as session:
        rows = (
            (
                await session.execute(
                    select(POI, ProfilePOI)
                    .join(ProfilePOI, ProfilePOI.poi_id == POI.id)
                    .where(ProfilePOI.profile_id == profile.id, POI.user_id == user.id)
                    .order_by(ProfilePOI.priority.asc(), POI.id.asc())
                )
            )
            .all()
        )

    if not rows:
        await message.answer(
            f"У профиля <b>{html.escape(profile.alias)}</b> пока нет точек назначения.\n"
            "Добавить: /poi_add\n"
            "Быстро одну точку «{DEFAULT}»: /set_destination и геолокация.".format(
                DEFAULT=html.escape(DESTINATION_LABEL)
            ),
            parse_mode="HTML",
        )
        return

    lines = [f"📍 Точки для профиля <b>{html.escape(profile.alias)}</b> (активный — последний по дате создания):\n"]
    keyboard_rows = []
    for poi, pp in rows:
        lines.append(
            f"• <b>{html.escape(poi.label)}</b> — макс. {pp.max_travel_min} мин, "
            f"приоритет <code>{pp.priority}</code>, {_mode_title(pp.mode)}"
        )
        keyboard_rows.extend(kb_poi_actions_rows(poi.id))

    lines.append("\nДобавить ещё: /poi_add · отмена мастера: /cancel")
    await message.answer(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
    )


@router.message(Command("poi_add"))
async def cmd_poi_add(message: types.Message, state: FSMContext):
    pair = await _active_user_profile(message.from_user.id)
    if not pair:
        await message.answer("Сначала /start и профиль /new_profile.")
        return
    await state.clear()
    await state.set_state(PoiStates.add_label)
    await message.answer(
        "➕ Новая точка для <b>текущего</b> профиля (последний созданный).\n\n"
        "Введите <b>краткое название</b> (например: «Работа», «Детсад»). "
        "Оно должно быть уникальным среди ваших точек.\n\n"
        "/cancel — выйти.",
        parse_mode="HTML",
    )


@router.message(PoiStates.add_label, F.text.startswith("/"))
async def poi_add_label_ignore_commands(message: types.Message):
    await message.answer("Сейчас жду название точки текстом (не команду). Или /cancel.")


@router.message(PoiStates.add_label, F.text)
async def poi_add_label(message: types.Message, state: FSMContext):
    label = (message.text or "").strip()
    if not label or len(label) > 200:
        await message.answer("Название: от 1 до 200 символов. Попробуйте ещё раз.")
        return
    await state.update_data(poi_label=label)
    await state.set_state(PoiStates.add_location)
    await message.answer(
        "Отправьте <b>геолокацию</b> этой точки (📎 → «Геолокация»).\n/cancel — отмена.",
        parse_mode="HTML",
    )


@router.message(PoiStates.add_location, F.location)
async def poi_add_location(message: types.Message, state: FSMContext):
    loc = message.location
    if not loc:
        return
    await state.update_data(poi_lat=float(loc.latitude), poi_lng=float(loc.longitude))
    await state.set_state(PoiStates.add_max_travel)
    await message.answer(
        "Максимум минут в пути до этой точки (целое число, например <code>45</code>).\n"
        "Или отправьте <code>-</code> для значения по умолчанию <b>60</b>.\n/cancel",
        parse_mode="HTML",
    )


@router.message(PoiStates.add_max_travel, F.text)
async def poi_add_max_travel(message: types.Message, state: FSMContext):
    raw = (message.text or "").strip()
    if raw == "-":
        max_t = 60
    else:
        try:
            max_t = int(raw)
        except ValueError:
            await message.answer("Нужно целое число минут или «-». Попробуйте снова.")
            return
    if max_t < 1 or max_t > 720:
        await message.answer("Допустимо от 1 до 720 минут.")
        return
    await state.update_data(poi_max_travel=max_t)
    pair = await _active_user_profile(message.from_user.id)
    if not pair:
        await state.clear()
        await message.answer("Профиль не найден. /start")
        return
    _, profile = pair
    async with async_session() as session:
        nxt = await _next_default_priority(session, profile.id)
    await state.update_data(poi_priority_default=nxt)
    await state.set_state(PoiStates.add_priority)
    await message.answer(
        f"Приоритет (меньшее число = важнее при отображении). "
        f"По умолчанию для новой точки: <b>{nxt}</b>.\n"
        "Введите целое число или <code>-</code> для значения по умолчанию.\n/cancel",
        parse_mode="HTML",
    )


@router.message(PoiStates.add_priority, F.text)
async def poi_add_priority(message: types.Message, state: FSMContext):
    raw = (message.text or "").strip()
    data = await state.get_data()
    default_pri = int(data.get("poi_priority_default") or 1)
    if raw == "-":
        pri = default_pri
    else:
        try:
            pri = int(raw)
        except ValueError:
            await message.answer("Нужно целое число приоритета или «-».")
            return
    if pri < 0 or pri > 9999:
        await message.answer("Приоритет: от 0 до 9999.")
        return
    await state.update_data(poi_priority=pri)
    await state.set_state(PoiStates.add_mode)
    await message.answer(
        "Режим расчёта времени в пути (для 2GIS сейчас используется маршрут ОТ; "
        "«пешком» сохраняется в профиле для совместимости):\n"
        "Выберите кнопку ниже.\n/cancel",
        reply_markup=kb_add_mode(),
    )


@router.callback_query(F.data.startswith("pam:"), StateFilter(PoiStates.add_mode))
async def poi_add_mode_callback(query: types.CallbackQuery, state: FSMContext):
    parts = query.data.split(":", 1)
    if len(parts) != 2 or parts[1] not in ("masstransit", "pedestrian"):
        await query.answer("Неизвестный режим", show_alert=True)
        return
    mode = parts[1]
    data = await state.get_data()
    label = data.get("poi_label")
    lat, lng = data.get("poi_lat"), data.get("poi_lng")
    max_t = data.get("poi_max_travel", 60)
    pri = data.get("poi_priority", 1)
    if label is None or lat is None or lng is None:
        await query.answer("Сессия сброшена. Начните с /poi_add", show_alert=True)
        await state.clear()
        return

    pair = await _active_user_profile(query.from_user.id)
    if not pair:
        await query.answer("Нет пользователя/профиля", show_alert=True)
        await state.clear()
        return
    user, profile = pair

    async with async_session() as session:
        poi = POI(user_id=user.id, label=label, lat=lat, lng=lng)
        session.add(poi)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            await query.answer()
            await query.message.answer(
                "Точка с таким названием уже существует. /cancel и /poi_add с другим именем."
            )
            await state.clear()
            return

        stmt = (
            insert(ProfilePOI)
            .values(
                profile_id=profile.id,
                poi_id=poi.id,
                max_travel_min=int(max_t),
                mode=mode,
                priority=int(pri),
            )
            .on_conflict_do_update(
                index_elements=["profile_id", "poi_id"],
                set_={
                    "max_travel_min": int(max_t),
                    "mode": mode,
                    "priority": int(pri),
                },
            )
        )
        await session.execute(stmt)
        await session.commit()

    await state.clear()
    await query.answer()
    await query.message.answer(
        f"✅ Точка «{html.escape(label)}» добавлена к профилю «{html.escape(profile.alias)}»: "
        f"макс. {max_t} мин, приоритет {pri}, {_mode_title(mode)}.\n/pois — список."
    )


@router.message(PoiStates.add_mode, F.text)
async def poi_add_mode_text_instead_of_button(message: types.Message):
    await message.answer("Выберите режим кнопкой под предыдущим сообщением или /cancel.")


@router.callback_query(F.data.startswith("pxn:"))
async def cb_edit_label_start(query: types.CallbackQuery, state: FSMContext):
    try:
        poi_id = int(query.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await query.answer()
        return
    pair = await _active_user_profile(query.from_user.id)
    if not pair:
        await query.answer("Нет профиля", show_alert=True)
        return
    user, profile = pair
    async with async_session() as session:
        row = await _profile_poi_for_user(session, profile_id=profile.id, user_id=user.id, poi_id=poi_id)
    if not row:
        await query.answer("Точка не найдена", show_alert=True)
        return
    await state.set_state(PoiStates.edit_label)
    await state.update_data(edit_poi_id=poi_id)
    await query.answer()
    await query.message.answer(
        f"Новое название для точки (сейчас: «{html.escape(row[0].label)}»).\n"
        "От 1 до 200 символов, уникально среди ваших точек.\n/cancel",
        parse_mode="HTML",
    )


@router.message(PoiStates.edit_label, F.text.startswith("/"))
async def poi_edit_label_ignore_commands(message: types.Message):
    await message.answer("Сейчас жду новое название текстом (не команду). Или /cancel.")


@router.message(PoiStates.edit_label, F.text)
async def poi_edit_label(message: types.Message, state: FSMContext):
    data = await state.get_data()
    poi_id = data.get("edit_poi_id")
    if not poi_id:
        await state.clear()
        await message.answer("Сессия сбита. /pois")
        return
    new_label = (message.text or "").strip()
    if not new_label or len(new_label) > 200:
        await message.answer("Название: от 1 до 200 символов.")
        return

    pair = await _active_user_profile(message.from_user.id)
    if not pair:
        await state.clear()
        await message.answer("Нет профиля.")
        return
    user, profile = pair

    async with async_session() as session:
        row = await _profile_poi_for_user(session, profile_id=profile.id, user_id=user.id, poi_id=int(poi_id))
        if not row:
            await state.clear()
            await message.answer("Точка не найдена.")
            return
        poi, _pp = row
        if new_label == poi.label:
            await state.clear()
            await message.answer("Название без изменений. /pois")
            return
        taken = await session.execute(
            select(POI.id).where(POI.user_id == user.id, POI.label == new_label, POI.id != poi.id)
        )
        if taken.scalar_one_or_none() is not None:
            await message.answer("У вас уже есть точка с таким названием. Введите другое.")
            return
        poi.label = new_label
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            await message.answer("Такое название уже занято. Введите другое.")
            return

    await state.clear()
    await message.answer(f"✅ Название обновлено: «{html.escape(new_label)}».\n/pois — список.", parse_mode="HTML")


@router.callback_query(F.data.startswith("pxm:"))
async def cb_edit_max_start(query: types.CallbackQuery, state: FSMContext):
    try:
        poi_id = int(query.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await query.answer()
        return
    pair = await _active_user_profile(query.from_user.id)
    if not pair:
        await query.answer("Нет профиля", show_alert=True)
        return
    user, profile = pair
    async with async_session() as session:
        row = await _profile_poi_for_user(session, profile_id=profile.id, user_id=user.id, poi_id=poi_id)
    if not row:
        await query.answer("Точка не найдена", show_alert=True)
        return
    await state.set_state(PoiStates.edit_numeric)
    await state.update_data(edit_op="max", edit_poi_id=poi_id)
    await query.answer()
    await query.message.answer(
        f"Новый максимум минут в пути для «{html.escape(row[0].label)}» (1–720, целое число):",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("pxp:"))
async def cb_edit_priority_start(query: types.CallbackQuery, state: FSMContext):
    try:
        poi_id = int(query.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await query.answer()
        return
    pair = await _active_user_profile(query.from_user.id)
    if not pair:
        await query.answer("Нет профиля", show_alert=True)
        return
    user, profile = pair
    async with async_session() as session:
        row = await _profile_poi_for_user(session, profile_id=profile.id, user_id=user.id, poi_id=poi_id)
    if not row:
        await query.answer("Точка не найдена", show_alert=True)
        return
    await state.set_state(PoiStates.edit_numeric)
    await state.update_data(edit_op="priority", edit_poi_id=poi_id)
    await query.answer()
    await query.message.answer(
        f"Новый приоритет для «{html.escape(row[0].label)}» (0–9999, меньше = важнее):",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("pxr:"))
async def cb_edit_mode_choose(query: types.CallbackQuery):
    try:
        poi_id = int(query.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await query.answer()
        return
    pair = await _active_user_profile(query.from_user.id)
    if not pair:
        await query.answer("Нет профиля", show_alert=True)
        return
    user, profile = pair
    async with async_session() as session:
        row = await _profile_poi_for_user(session, profile_id=profile.id, user_id=user.id, poi_id=poi_id)
    if not row:
        await query.answer("Точка не найдена", show_alert=True)
        return
    await query.answer()
    await query.message.answer(
        f"Режим для «{html.escape(row[0].label)}»:", parse_mode="HTML", reply_markup=kb_poi_mode_choice(poi_id)
    )


@router.callback_query(F.data.startswith("pmod:"))
async def cb_edit_mode_set(query: types.CallbackQuery):
    parts = (query.data or "").split(":")
    if len(parts) != 3 or parts[2] not in ("masstransit", "pedestrian"):
        await query.answer()
        return
    try:
        poi_id = int(parts[1])
    except ValueError:
        await query.answer()
        return
    mode = parts[2]
    pair = await _active_user_profile(query.from_user.id)
    if not pair:
        await query.answer("Нет профиля", show_alert=True)
        return
    user, profile = pair
    async with async_session() as session:
        row = await _profile_poi_for_user(session, profile_id=profile.id, user_id=user.id, poi_id=poi_id)
        if not row:
            await query.answer("Точка не найдена", show_alert=True)
            return
        await session.execute(
            insert(ProfilePOI)
            .values(
                profile_id=profile.id,
                poi_id=poi_id,
                max_travel_min=row[1].max_travel_min,
                mode=mode,
                priority=row[1].priority,
            )
            .on_conflict_do_update(
                index_elements=["profile_id", "poi_id"],
                set_={"mode": mode},
            )
        )
        await session.commit()
    await query.answer()
    await query.message.answer(f"✅ Режим: {_mode_title(mode)}.")


@router.callback_query(F.data.startswith("pxd:"))
async def cb_delete_poi_link(query: types.CallbackQuery):
    try:
        poi_id = int(query.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await query.answer()
        return
    pair = await _active_user_profile(query.from_user.id)
    if not pair:
        await query.answer("Нет профиля", show_alert=True)
        return
    user, profile = pair
    label = ""
    async with async_session() as session:
        row = await _profile_poi_for_user(session, profile_id=profile.id, user_id=user.id, poi_id=poi_id)
        if not row:
            await query.answer("Точка не найдена", show_alert=True)
            return
        label = row[0].label
        n_links = await session.scalar(
            select(func.count()).select_from(ProfilePOI).where(ProfilePOI.poi_id == poi_id)
        )
        await session.execute(
            delete(ProfilePOI).where(
                ProfilePOI.profile_id == profile.id,
                ProfilePOI.poi_id == poi_id,
            )
        )
        deleted_poi_row = n_links == 1
        if deleted_poi_row:
            await session.execute(delete(POI).where(POI.id == poi_id, POI.user_id == user.id))
        await session.commit()
    await query.answer()
    extra = " Запись точки в базе удалена (нигде больше не использовалась)." if deleted_poi_row else ""
    await query.message.answer(
        f"🗑 Точка «{html.escape(label)}» отвязана от профиля.{extra}",
        parse_mode="HTML",
    )


@router.message(PoiStates.edit_numeric, F.text)
async def poi_edit_numeric(message: types.Message, state: FSMContext):
    data = await state.get_data()
    op = data.get("edit_op")
    poi_id = data.get("edit_poi_id")
    if op not in ("max", "priority") or not poi_id:
        await state.clear()
        await message.answer("Сессия сбита. /pois")
        return
    try:
        val = int((message.text or "").strip())
    except ValueError:
        await message.answer("Нужно целое число.")
        return
    if op == "max":
        if val < 1 or val > 720:
            await message.answer("От 1 до 720.")
            return
        field = "max_travel_min"
    else:
        if val < 0 or val > 9999:
            await message.answer("От 0 до 9999.")
            return
        field = "priority"

    pair = await _active_user_profile(message.from_user.id)
    if not pair:
        await state.clear()
        await message.answer("Нет профиля.")
        return
    user, profile = pair
    async with async_session() as session:
        row = await _profile_poi_for_user(session, profile_id=profile.id, user_id=user.id, poi_id=int(poi_id))
        if not row:
            await state.clear()
            await message.answer("Точка не найдена.")
            return
        poi, pp = row
        kwargs = {
            "profile_id": profile.id,
            "poi_id": int(poi_id),
            "max_travel_min": pp.max_travel_min,
            "mode": pp.mode,
            "priority": pp.priority,
        }
        kwargs[field] = val
        await session.execute(
            insert(ProfilePOI)
            .values(**kwargs)
            .on_conflict_do_update(
                index_elements=["profile_id", "poi_id"],
                set_={field: val},
            )
        )
        await session.commit()
    await state.clear()
    await message.answer(
        f"✅ Для «{html.escape(poi.label)}» обновлено: "
        f"{'макс. минут' if op == 'max' else 'приоритет'} = <b>{val}</b>.",
        parse_mode="HTML",
    )


@router.message(Command("set_destination"))
async def cmd_set_destination(message: types.Message):
    await message.answer(
        "📍 <b>Быстрая</b> одна точка с названием «Пункт назначения»:\n"
        "отправьте <b>геолокацию</b> (📎 → «Геолокация»).\n\n"
        "Несколько точек, лимиты минут и приоритеты: /poi_add и /pois.",
        parse_mode="HTML",
    )


@router.message(StateFilter(None), F.location)
async def on_location_legacy(message: types.Message):
    """Геолокация без активного FSM — прежнее поведение «Пункт назначения»."""
    loc = message.location
    if not loc or not loc.latitude or not loc.longitude:
        await message.answer("Не удалось получить координаты.")
        return

    lat = float(loc.latitude)
    lng = float(loc.longitude)
    tg_user_id = message.from_user.id

    async with async_session() as session:
        user_res = await session.execute(select(User).where(User.tg_user_id == tg_user_id))
        user = user_res.scalar_one_or_none()
        if not user:
            await message.answer("Сначала нажмите /start")
            return

        profile_res = await session.execute(
            select(Profile).where(Profile.user_id == user.id).order_by(desc(Profile.created_at)).limit(1)
        )
        profile = profile_res.scalar_one_or_none()
        if not profile:
            await message.answer("Создайте профиль через /new_profile, затем снова отправьте геолокацию.")
            return

        poi_res = await session.execute(
            select(POI).where(POI.user_id == user.id, POI.label == DESTINATION_LABEL)
        )
        poi = poi_res.scalar_one_or_none()
        if poi:
            poi.lat = lat
            poi.lng = lng
        else:
            poi = POI(user_id=user.id, label=DESTINATION_LABEL, lat=lat, lng=lng)
            session.add(poi)
            await session.flush()

        stmt = insert(ProfilePOI).values(
            profile_id=profile.id,
            poi_id=poi.id,
            max_travel_min=60,
            mode="masstransit",
            priority=1,
        ).on_conflict_do_update(
            index_elements=["profile_id", "poi_id"],
            set_={
                "max_travel_min": 60,
                "mode": "masstransit",
            },
        )
        await session.execute(stmt)
        await session.commit()

    await message.answer(
        "✅ «Пункт назначения» сохранён (60 мин, ОТ, приоритет 1). "
        "Полный список и правки: /pois"
    )
