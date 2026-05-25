"""
/train — запуск полного ML-пайплайна прямо из бота.
Stage A → Stage B (если есть дуэли) → Скоринг → Результат.

Синтетические дуэли НЕ генерируются автоматически.
Запускайте вручную: python3 -m scripts.ml.synthesize_pairwise --profile-id N --duels K
"""
from __future__ import annotations

import asyncio
import html
import traceback

from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select, func

from core.session import async_session
from models import Profile, Rating, User
from services.ml.pipeline import PipelineResult, run_full_pipeline

router = Router()

# Параметры пайплайна по умолчанию
DEFAULT_STAGE_A_EPOCHS = 80
DEFAULT_STAGE_B_EPOCHS = 30
DEFAULT_STAGE_B_LR = 1e-4


def _confirm_kb(profile_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🚀 Да, обучить", callback_data=f"train_run:{profile_id}"),
            InlineKeyboardButton(text="❌ Отмена",        callback_data="train_cancel"),
        ]
    ])


def _format_result(result: PipelineResult) -> str:
    a = result.stage_a
    b = result.stage_b

    def fmt(v, decimals=3):
        return f"{v:.{decimals}f}" if v is not None else "—"

    def gap_emoji(train, val):
        """Подсветка переобучения: |val - train| на val-шкале."""
        if train is None or val is None:
            return ""
        gap = val - train
        if gap < 0.1: return "✅"
        if gap < 0.25: return "⚠️"
        return "🔥"

    def quality_emoji(v):
        if v is None: return ""
        if v < 0.6: return "🟢"
        if v < 0.9: return "🟡"
        return "🔴"

    def head_line(emoji: str, name: str, train, val) -> str:
        g = gap_emoji(train, val)
        gap_txt = f" gap {val - train:+.2f}" if (train is not None and val is not None) else ""
        return (
            f"   {quality_emoji(val)} {emoji} {name}: "
            f"<b>{fmt(val)}</b> val / {fmt(train)} train {g}{gap_txt}\n"
        )

    best_ep_txt = f"   best epoch: {a.best_epoch + 1}/80\n" if a.best_epoch is not None else ""

    if b.skipped:
        stage_b_block = (
            "⚡️ <b>Stage B — ранжирование</b>\n"
            "   ⏭ пропущен (нет дуэлей)\n"
            "   Сгенерируйте пары: <code>python3 -m scripts.ml.synthesize_pairwise --profile-id N --duels K</code>\n"
            "   Или соберите реальные через /duel\n\n"
        )
    else:
        stage_b_block = (
            "⚡️ <b>Stage B — ранжирование</b>\n"
            f"   пар: {b.n_pairs}\n"
            f"   pairwise loss: <b>{fmt(b.train_loss)}</b>\n\n"
        )

    return (
        "╔══════════════════════════════╗\n"
        "║     🎓  Обучение завершено     ║\n"
        "╚══════════════════════════════╝\n\n"

        "📚 <b>Stage A — регрессия</b>\n"
        f"   Данные:  {a.n_train} train / {a.n_val} val\n"
        f"   train loss:  {fmt(a.train_loss)}\n"
        f"{best_ep_txt}\n"
        f"{head_line('💄', 'beauty MAE  ', a.train_mae_beauty, a.val_mae_beauty)}"
        f"{head_line('💰', 'pq MAE      ', a.train_mae_pq,     a.val_mae_pq)}"
        f"{head_line('🚌', 'distance MAE', a.train_mae_dist,   a.val_mae_dist)}\n"

        f"{stage_b_block}"

        f"🏆 Проскорировано квартир: <b>{result.scored_count}</b>\n\n"

        "MAE: 🟢 ≤0.6  🟡 0.6–0.9  🔴 ≥0.9\n"
        "gap: ✅ &lt;0.1  ⚠️ 0.1–0.25  🔥 ≥0.25 (переобучение)\n\n"
        "Готово! Смотрите топ: /top"
    )


@router.message(Command("train"))
async def cmd_train(message: types.Message) -> None:
    tg_id = message.from_user.id
    async with async_session() as session:
        user = (await session.execute(
            select(User).where(User.tg_user_id == tg_id)
        )).scalar_one_or_none()
        if not user:
            await message.answer("Сначала /start")
            return

        profile = (await session.execute(
            select(Profile).where(Profile.user_id == user.id).order_by(Profile.id.desc()).limit(1)
        )).scalar_one_or_none()
        if not profile:
            await message.answer("Нет профиля. Создайте через /new_profile.")
            return

        n_rated = (await session.execute(
            select(func.count()).select_from(Rating)
            .where(Rating.profile_id == profile.id)
            .where(Rating.skipped.is_(False))
            .where(
                (Rating.beauty.is_not(None))
                | (Rating.price_quality.is_not(None))
                | (Rating.distance_pref.is_not(None))
            )
        )).scalar() or 0

    if n_rated < 50:
        await message.answer(
            f"⚠️ Слишком мало оценок для обучения: <b>{n_rated}</b>.\n"
            "Нужно минимум 50 — продолжайте оценивать через /next.",
            parse_mode="HTML",
        )
        return

    await message.answer(
        f"🎓 <b>Запустить обучение модели?</b>\n\n"
        f"📊 Профиль: <b>#{profile.id}</b> «{profile.alias}»\n"
        f"📝 Размеченных квартир: <b>{n_rated}</b>\n\n"
        f"⏱ Займёт ~15–30 секунд.",
        parse_mode="HTML",
        reply_markup=_confirm_kb(profile.id),
    )


@router.callback_query(F.data.startswith("train_run:"))
async def cb_train_run(callback: types.CallbackQuery) -> None:
    await callback.answer()
    profile_id = int(callback.data.split(":")[1])

    # Редактируем сообщение в «прогресс-бар»
    status_msg = await callback.message.edit_text("⏳ Запускаю обучение...")

    async def progress(text: str) -> None:
        try:
            await status_msg.edit_text(f"⏳ {text}")
        except Exception:
            pass

    try:
        result = await run_full_pipeline(
            profile_id=profile_id,
            stage_a_epochs=DEFAULT_STAGE_A_EPOCHS,
            stage_a_lr=1e-3,
            stage_b_epochs=DEFAULT_STAGE_B_EPOCHS,
            stage_b_lr=DEFAULT_STAGE_B_LR,
            progress=progress,
        )
        await status_msg.edit_text(
            _format_result(result),
            parse_mode="HTML",
        )
    except ValueError as e:
        await status_msg.edit_text(
            f"❌ Ошибка:\n<code>{html.escape(str(e))}</code>",
            parse_mode="HTML",
        )
    except Exception as e:
        tb = html.escape(traceback.format_exc()[-1200:])
        try:
            await status_msg.edit_text(
                f"❌ Неожиданная ошибка:\n<code>{tb}</code>",
                parse_mode="HTML",
            )
        except Exception:
            # Если сообщение слишком длинное — отправить коротко
            await status_msg.edit_text(
                f"❌ Ошибка: <code>{html.escape(str(e))}</code>",
                parse_mode="HTML",
            )


@router.callback_query(F.data == "train_cancel")
async def cb_train_cancel(callback: types.CallbackQuery) -> None:
    await callback.answer("Отменено")
    await callback.message.edit_text("❌ Обучение отменено.")
