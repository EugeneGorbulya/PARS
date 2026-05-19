"""
Управление профилем: веса, публикация, форк, список профилей.

Команды:
  /weights         — показать текущие веса и поменять (FSM, ожидает 3 числа)
  /publish         — опубликовать активный профиль, сгенерировать slug
  /unpublish       — снять активный профиль с публикации
  /fork <slug>     — скопировать публичный профиль (профиль + POI + веса)
  /browse_profiles — список публичных профилей с их slug'ами
  /profiles        — список собственных профилей пользователя (с пометкой активного)

Активным считается самый поздно созданный профиль пользователя — так уже
работают handler'ы /next, /top, /duel и т.д., этот файл следует тому же
соглашению.
"""
from __future__ import annotations

import re
import secrets
from typing import Optional, Tuple

from aiogram import Router, types
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from sqlalchemy import select, desc, func, update

from core.session import async_session
from models import (
    HiddenFlat,
    ModelSnapshot,
    PairwiseRating,
    POI,
    Profile,
    ProfileFlatScore,
    ProfilePOI,
    Rating,
    SavedFlat,
    SeenFlat,
    User,
)
from services.s3.client import S3Client


router = Router()


class ManageStates(StatesGroup):
    waiting_for_weights = State()


# ──────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────

async def _active_user_profile(tg_user_id: int) -> Optional[Tuple[User, Profile]]:
    async with async_session() as session:
        user = (await session.execute(
            select(User).where(User.tg_user_id == tg_user_id)
        )).scalar_one_or_none()
        if not user:
            return None
        profile = (await session.execute(
            select(Profile)
            .where(Profile.user_id == user.id)
            .order_by(desc(Profile.created_at))
            .limit(1)
        )).scalar_one_or_none()
        if not profile:
            return None
        return user, profile


_SLUG_CLEAN_RE = re.compile(r"[^a-z0-9-]+")


def make_slug(alias: str) -> str:
    """`Центр Москвы` → `profile-ab12cd` (нелатинские символы выкидываем,
    к ASCII-основе добавляем 6-символьный случайный суффикс)."""
    base = alias.strip().lower().replace(" ", "-")
    base = _SLUG_CLEAN_RE.sub("", base).strip("-")
    if not base:
        base = "profile"
    base = base[:32]
    suffix = secrets.token_hex(3)
    return f"{base}-{suffix}"


async def _unique_alias(session, user_id: int, base: str) -> str:
    candidate = base
    for i in range(2, 100):
        exists = (await session.execute(
            select(Profile.id).where(
                Profile.user_id == user_id,
                Profile.alias == candidate,
            )
        )).scalar_one_or_none()
        if not exists:
            return candidate
        candidate = f"{base} ({i})"
    return f"{base} ({secrets.token_hex(3)})"


# ──────────────────────────────────────────────
# /weights
# ──────────────────────────────────────────────

@router.message(Command("weights"))
async def cmd_weights(message: types.Message, state: FSMContext):
    pair = await _active_user_profile(message.from_user.id)
    if not pair:
        await message.answer("Сначала /start и /new_profile.")
        return
    _, profile = pair
    await message.answer(
        f"⚖️ Веса профиля <b>{profile.alias}</b>:\n"
        f"  • Beauty:        <b>{float(profile.weight_beauty):.2f}</b>\n"
        f"  • Price/Quality: <b>{float(profile.weight_price_quality):.2f}</b>\n"
        f"  • Distance:      <b>{float(profile.weight_distance):.2f}</b>\n\n"
        "Отправьте 3 новых значения через пробел: <code>beauty pq distance</code>.\n"
        "Сумма должна быть ≈ 1.0.\n\n"
        "Пример: <code>0.5 0.2 0.3</code>\n"
        "Отмена: /cancel"
    )
    await state.set_state(ManageStates.waiting_for_weights)


@router.message(ManageStates.waiting_for_weights, Command("cancel"))
async def cmd_weights_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено.")


@router.message(ManageStates.waiting_for_weights)
async def process_weights(message: types.Message, state: FSMContext):
    parts = (message.text or "").strip().replace(",", ".").split()
    if len(parts) != 3:
        await message.answer("⚠️ Нужно ровно 3 числа через пробел. Попробуйте ещё раз или /cancel.")
        return
    try:
        wb, wpq, wd = (float(p) for p in parts)
    except ValueError:
        await message.answer("⚠️ Не могу распарсить числа. Пример: <code>0.5 0.2 0.3</code>")
        return
    if min(wb, wpq, wd) < 0:
        await message.answer("⚠️ Веса должны быть неотрицательными.")
        return
    s = wb + wpq + wd
    if abs(s - 1.0) > 0.05:
        await message.answer(
            f"⚠️ Сумма весов = {s:.3f}, нужно ≈ 1.0 (допуск 0.05).\n"
            "Попробуйте ещё раз или /cancel."
        )
        return

    pair = await _active_user_profile(message.from_user.id)
    if not pair:
        await message.answer("Профиль не найден.")
        await state.clear()
        return
    _, profile = pair

    async with async_session() as session:
        prof = (await session.execute(
            select(Profile).where(Profile.id == profile.id)
        )).scalar_one()
        prof.weight_beauty = wb
        prof.weight_price_quality = wpq
        prof.weight_distance = wd

        # Перепересчитываем score у уже посчитанных квартир без перетренировки:
        # per-head предсказания не меняются, меняется только их линейная комбинация.
        rescored = (await session.execute(
            update(ProfileFlatScore)
            .where(ProfileFlatScore.profile_id == profile.id)
            .values(score=(
                func.coalesce(ProfileFlatScore.beauty_hat, 3.0) * wb
                + func.coalesce(ProfileFlatScore.price_quality_hat, 3.0) * wpq
                + func.coalesce(ProfileFlatScore.distance_hat, 3.0) * wd
            ))
        )).rowcount or 0
        await session.commit()

    rescore_line = (
        f"\n♻️ Пересчитан score у {rescored} квартир (без перетренировки)."
        if rescored else
        "\nℹ️ Чтобы получить рекомендации с новыми весами — запустите /train."
    )
    await message.answer(
        f"✅ Веса обновлены:\n"
        f"  • Beauty:        <b>{wb:.2f}</b>\n"
        f"  • Price/Quality: <b>{wpq:.2f}</b>\n"
        f"  • Distance:      <b>{wd:.2f}</b>"
        + rescore_line
        + "\n\nСмотреть рекомендации: /top"
    )
    await state.clear()


# ──────────────────────────────────────────────
# /publish · /unpublish
# ──────────────────────────────────────────────

@router.message(Command("publish"))
async def cmd_publish(message: types.Message):
    pair = await _active_user_profile(message.from_user.id)
    if not pair:
        await message.answer("Сначала /start и /new_profile.")
        return
    _, profile = pair

    async with async_session() as session:
        prof = (await session.execute(
            select(Profile).where(Profile.id == profile.id)
        )).scalar_one()
        if prof.is_public and prof.public_slug:
            await message.answer(
                f"📢 Профиль <b>{prof.alias}</b> уже опубликован.\n"
                f"Slug: <code>{prof.public_slug}</code>\n\n"
                f"Скопировать его можно командой:\n"
                f"<code>/fork {prof.public_slug}</code>\n\n"
                f"Снять с публикации: /unpublish"
            )
            return

        slug = None
        for _ in range(8):
            candidate = make_slug(prof.alias)
            exists = (await session.execute(
                select(Profile.id).where(Profile.public_slug == candidate)
            )).scalar_one_or_none()
            if not exists:
                slug = candidate
                break
        if not slug:
            await message.answer("Не удалось сгенерировать уникальный slug. Попробуйте ещё раз.")
            return

        prof.public_slug = slug
        prof.is_public = True
        await session.commit()

    await message.answer(
        f"📢 Профиль <b>{profile.alias}</b> опубликован.\n"
        f"Slug: <code>{slug}</code>\n\n"
        f"Поделитесь slug'ом — другие пользователи смогут скопировать ваш профиль:\n"
        f"<code>/fork {slug}</code>\n\n"
        f"Снять с публикации: /unpublish"
    )


@router.message(Command("unpublish"))
async def cmd_unpublish(message: types.Message):
    pair = await _active_user_profile(message.from_user.id)
    if not pair:
        await message.answer("Сначала /start и /new_profile.")
        return
    _, profile = pair

    async with async_session() as session:
        prof = (await session.execute(
            select(Profile).where(Profile.id == profile.id)
        )).scalar_one()
        if not prof.is_public:
            await message.answer("Профиль и так не опубликован.")
            return
        prof.is_public = False
        await session.commit()

    await message.answer(f"🔒 Профиль <b>{profile.alias}</b> снят с публикации.")


# ──────────────────────────────────────────────
# /fork
# ──────────────────────────────────────────────

@router.message(Command("fork"))
async def cmd_fork(message: types.Message, command: CommandObject):
    slug = (command.args or "").strip()
    if not slug:
        await message.answer(
            "Использование: <code>/fork &lt;slug&gt;</code>\n"
            "Посмотреть доступные профили: /browse_profiles"
        )
        return

    tg_user_id = message.from_user.id

    async with async_session() as session:
        user = (await session.execute(
            select(User).where(User.tg_user_id == tg_user_id)
        )).scalar_one_or_none()
        if not user:
            user = User(tg_user_id=tg_user_id, username=message.from_user.username or "Unknown")
            session.add(user)
            await session.commit()
            await session.refresh(user)

        src = (await session.execute(
            select(Profile).where(
                Profile.public_slug == slug,
                Profile.is_public.is_(True),
            )
        )).scalar_one_or_none()
        if not src:
            await message.answer(
                f"Профиль <code>{slug}</code> не найден или снят с публикации.\n"
                "Доступные профили: /browse_profiles"
            )
            return
        if src.user_id == user.id:
            await message.answer("Это ваш собственный профиль — форкать самого себя смысла нет 🙂")
            return

        new_alias = await _unique_alias(session, user.id, f"{src.alias} (fork)")
        new = Profile(
            user_id=user.id,
            alias=new_alias,
            city=src.city,
            cian_filter=dict(src.cian_filter),
            weight_beauty=src.weight_beauty,
            weight_price_quality=src.weight_price_quality,
            weight_distance=src.weight_distance,
            epsilon_explore=src.epsilon_explore,
            stage=src.stage,
            is_public=False,
            public_slug=None,
            forked_from_profile_id=src.id,
        )
        session.add(new)
        await session.flush()
        new_profile_id = new.id

        # 1) POI с дедупом по label у текущего пользователя
        src_pois = (await session.execute(
            select(POI, ProfilePOI)
            .join(ProfilePOI, ProfilePOI.poi_id == POI.id)
            .where(ProfilePOI.profile_id == src.id)
        )).all()
        copied_pois = 0
        for src_poi, src_link in src_pois:
            existing = (await session.execute(
                select(POI).where(
                    POI.user_id == user.id,
                    POI.label == src_poi.label,
                )
            )).scalar_one_or_none()
            if existing:
                target_poi = existing
            else:
                target_poi = POI(
                    user_id=user.id,
                    label=src_poi.label,
                    lat=src_poi.lat,
                    lng=src_poi.lng,
                )
                session.add(target_poi)
                await session.flush()
            session.add(ProfilePOI(
                profile_id=new_profile_id,
                poi_id=target_poi.id,
                max_travel_min=src_link.max_travel_min,
                mode=src_link.mode,
                priority=src_link.priority,
            ))
            copied_pois += 1

        # 2) Pointwise ratings (user_id переписан, profile_id новый)
        src_ratings = (await session.execute(
            select(Rating).where(
                Rating.user_id == src.user_id,
                Rating.profile_id == src.id,
            )
        )).scalars().all()
        for r in src_ratings:
            session.add(Rating(
                user_id=user.id,
                profile_id=new_profile_id,
                flat_id=r.flat_id,
                beauty=r.beauty,
                price_quality=r.price_quality,
                distance_pref=r.distance_pref,
                skipped=r.skipped,
                source=r.source,
            ))
        copied_ratings = len(src_ratings)

        # 3) Pairwise ratings
        src_pairs = (await session.execute(
            select(PairwiseRating).where(
                PairwiseRating.user_id == src.user_id,
                PairwiseRating.profile_id == src.id,
            )
        )).scalars().all()
        for p in src_pairs:
            session.add(PairwiseRating(
                user_id=user.id,
                profile_id=new_profile_id,
                flat_a_id=p.flat_a_id,
                flat_b_id=p.flat_b_id,
                factor=p.factor,
                preferred_flat_id=p.preferred_flat_id,
            ))
        copied_pairs = len(src_pairs)

        # 4) seen/saved/hidden (per-(user, profile, flat) бэги)
        copied_seen = await _copy_user_flat_table(
            session, SeenFlat, src.user_id, user.id, src.id, new_profile_id,
        )
        copied_saved = await _copy_user_flat_table(
            session, SavedFlat, src.user_id, user.id, src.id, new_profile_id,
        )
        copied_hidden = await _copy_user_flat_table(
            session, HiddenFlat, src.user_id, user.id, src.id, new_profile_id,
        )

        # 5) ProfileFlatScore — profile-scoped, user_id нет
        src_scores = (await session.execute(
            select(ProfileFlatScore).where(ProfileFlatScore.profile_id == src.id)
        )).scalars().all()
        for s in src_scores:
            session.add(ProfileFlatScore(
                profile_id=new_profile_id,
                flat_id=s.flat_id,
                score=s.score,
                beauty_hat=s.beauty_hat,
                price_quality_hat=s.price_quality_hat,
                distance_hat=s.distance_hat,
            ))
        copied_scores = len(src_scores)

        # 6) Последний снапшот модели — копируем .pt в S3 и создаём строку
        copied_snapshot = await _clone_latest_snapshot(session, src.id, new_profile_id)
        if copied_snapshot is not None:
            new.last_trained_snapshot_id = copied_snapshot

        await session.commit()

    lines = [
        f"✅ Профиль <b>{new_alias}</b> скопирован из <code>{slug}</code>.",
        "",
        "Перенесено:",
        f"  • POI: <b>{copied_pois}</b>",
        f"  • оценки квартир: <b>{copied_ratings}</b>",
        f"  • дуэли: <b>{copied_pairs}</b>",
        f"  • просмотренные/сохранённые/скрытые: <b>{copied_seen}/{copied_saved}/{copied_hidden}</b>",
        f"  • кэш скоров: <b>{copied_scores}</b>",
        f"  • снапшот модели: <b>{'да' if copied_snapshot else 'нет'}</b>",
        "",
        "Профиль теперь активен. Дальше:",
        "  • /top — посмотреть рекомендации с уже обученной моделью" if copied_snapshot else "  • /train — обучить модель на оценках автора",
        "  • /next — продолжить оценивать (свои оценки добавятся к скопированным)",
        "  • /weights — поднастроить веса под себя",
    ]
    await message.answer("\n".join(lines))


async def _copy_user_flat_table(
    session, model_cls, src_user_id: int, dst_user_id: int,
    src_profile_id: int, dst_profile_id: int,
) -> int:
    """Копирует строки таблиц с PK = (user_id, profile_id, flat_id)."""
    rows = (await session.execute(
        select(model_cls.flat_id).where(
            model_cls.user_id == src_user_id,
            model_cls.profile_id == src_profile_id,
        )
    )).scalars().all()
    for flat_id in rows:
        session.add(model_cls(
            user_id=dst_user_id,
            profile_id=dst_profile_id,
            flat_id=flat_id,
        ))
    return len(rows)


async def _clone_latest_snapshot(session, src_profile_id: int, dst_profile_id: int) -> Optional[int]:
    """Берёт последний снапшот src профиля, копирует .pt в S3 и создаёт новую строку в model_snapshots.
    Возвращает id новой строки или None, если у источника нет снапшотов / S3 недоступен."""
    src_snap = (await session.execute(
        select(ModelSnapshot)
        .where(ModelSnapshot.profile_id == src_profile_id)
        .order_by(desc(ModelSnapshot.created_at))
        .limit(1)
    )).scalar_one_or_none()
    if not src_snap:
        return None

    s3 = S3Client()
    try:
        data = await s3.download_bytes(s3_uri=src_snap.storage_uri)
    except Exception:
        # модель не скопировалась — это не фатально, просто скажем "нет"
        return None
    new_key = f"snapshots/profile_{dst_profile_id}/forked_from_{src_snap.id}_{secrets.token_hex(4)}.pt"
    new_uri = await s3.upload_bytes(data, new_key, content_type="application/octet-stream")

    new_snap = ModelSnapshot(
        profile_id=dst_profile_id,
        backbone=src_snap.backbone,
        head_type=src_snap.head_type,
        storage_uri=new_uri,
        metrics=src_snap.metrics,
        kendall_tau_top20=src_snap.kendall_tau_top20,
        mae=src_snap.mae,
    )
    session.add(new_snap)
    await session.flush()
    return new_snap.id


# ──────────────────────────────────────────────
# /browse_profiles
# ──────────────────────────────────────────────

@router.message(Command("browse_profiles"))
async def cmd_browse_profiles(message: types.Message):
    async with async_session() as session:
        fork_counts = (
            select(
                Profile.forked_from_profile_id.label("src_id"),
                func.count(Profile.id).label("n_forks"),
            )
            .where(Profile.forked_from_profile_id.is_not(None))
            .group_by(Profile.forked_from_profile_id)
            .subquery()
        )
        rows = (await session.execute(
            select(Profile, User.username, fork_counts.c.n_forks)
            .join(User, User.id == Profile.user_id)
            .outerjoin(fork_counts, fork_counts.c.src_id == Profile.id)
            .where(Profile.is_public.is_(True))
            .order_by(
                func.coalesce(fork_counts.c.n_forks, 0).desc(),
                Profile.created_at.desc(),
            )
            .limit(20)
        )).all()

    if not rows:
        await message.answer(
            "Пока никто не опубликовал свой профиль 😶\n"
            "Будьте первым: /publish"
        )
        return

    lines = [f"📚 <b>Публичные профили</b> (топ {len(rows)}):\n"]
    for prof, username, n_forks in rows:
        wb = float(prof.weight_beauty)
        wpq = float(prof.weight_price_quality)
        wd = float(prof.weight_distance)
        owner = f"@{username}" if username and username != "Unknown" else "—"
        forks_str = f" · 🔀 {int(n_forks)} форк" if n_forks else ""
        lines.append(
            f"<code>{prof.public_slug}</code>{forks_str}\n"
            f"  <b>{prof.alias}</b> · {prof.city} · {owner}\n"
            f"  Веса: b {wb:.2f} / pq {wpq:.2f} / d {wd:.2f}\n"
            f"  Скопировать: <code>/fork {prof.public_slug}</code>\n"
        )
    await message.answer("\n".join(lines))


# ──────────────────────────────────────────────
# /profiles
# ──────────────────────────────────────────────

@router.message(Command("profiles"))
async def cmd_profiles(message: types.Message):
    pair = await _active_user_profile(message.from_user.id)
    if not pair:
        await message.answer("Сначала /start и /new_profile.")
        return
    user, active_profile = pair

    async with async_session() as session:
        all_profiles = (await session.execute(
            select(Profile)
            .where(Profile.user_id == user.id)
            .order_by(desc(Profile.created_at))
        )).scalars().all()

    lines = [f"📑 Ваши профили ({len(all_profiles)}):\n"]
    for prof in all_profiles:
        is_active = prof.id == active_profile.id
        prefix = "✅ " if is_active else "    "
        pub = " 📢" if prof.is_public else ""
        forked = " 🔀" if prof.forked_from_profile_id else ""
        wb = float(prof.weight_beauty)
        wpq = float(prof.weight_price_quality)
        wd = float(prof.weight_distance)
        lines.append(
            f"{prefix}<b>{prof.alias}</b>{pub}{forked}\n"
            f"     {prof.city} · веса b {wb:.2f} / pq {wpq:.2f} / d {wd:.2f}"
            + (f"\n     slug: <code>{prof.public_slug}</code>" if prof.public_slug else "")
        )
    lines.append(
        "\n✅ — активный профиль (последний созданный).\n"
        "📢 — опубликован · 🔀 — это форк.\n\n"
        "Создать новый: /new_profile · поменять веса: /weights"
    )
    await message.answer("\n\n".join(lines))
