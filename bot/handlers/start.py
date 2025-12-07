from aiogram import Router, types
from aiogram.filters import CommandStart
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.session import async_session
from models import User

router = Router()

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    tg_user_id = message.from_user.id
    username = message.from_user.username or "Unknown"

    async with async_session() as session:
        # Check if user exists
        result = await session.execute(select(User).where(User.tg_user_id == tg_user_id))
        user = result.scalar_one_or_none()

        if not user:
            user = User(tg_user_id=tg_user_id, username=username)
            session.add(user)
            await session.commit()
            await message.answer(
                f"Привет, {message.from_user.first_name}! 👋\n\n"
                "Я помогу тебе найти идеальную квартиру для аренды.\n"
                "Я не просто фильтрую по цене, а учусь понимать твой вкус по фотографиям.\n\n"
                "Нажми /new_profile чтобы начать поиск."
            )
        else:
            await message.answer(
                f"С возвращением, {message.from_user.first_name}!\n"
                "Продолжим поиск? Жми /next или посмотри /top."
            )

