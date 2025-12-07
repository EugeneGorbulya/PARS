import re
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from core.session import async_session
from models import User, Profile
from bot.states import ProfileStates
from bot.keyboards import profile_kb

router = Router()

@router.message(Command("new_profile"))
async def cmd_new_profile(message: types.Message, state: FSMContext):
    await message.answer(
        "🛠 Создание нового профиля поиска.\n\n"
        "Шаг 1. Введите город:",
        reply_markup=profile_kb.get_city_kb()
    )
    await state.set_state(ProfileStates.waiting_for_city)

@router.message(ProfileStates.waiting_for_city)
async def process_city(message: types.Message, state: FSMContext):
    city = message.text.strip()
    await state.update_data(city=city)
    
    await message.answer(
        "Шаг 2. Введите диапазон цен (тыс. руб) через пробел.\n"
        "Например: 40 90\n"
        "(От 40 000 до 90 000 руб)",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(ProfileStates.waiting_for_price)

@router.message(ProfileStates.waiting_for_price)
async def process_price(message: types.Message, state: FSMContext):
    try:
        parts = message.text.replace("k", "").replace("к", "").split()
        if len(parts) != 2:
            raise ValueError
        min_p = int(parts[0]) * 1000
        max_p = int(parts[1]) * 1000
        
        await state.update_data(min_price=min_p, max_price=max_p)
        
        await message.answer(
            "Шаг 3. Выберите количество комнат:",
            reply_markup=profile_kb.get_rooms_kb()
        )
        await state.set_state(ProfileStates.waiting_for_rooms)
    except ValueError:
        await message.answer("⚠️ Некорректный формат. Попробуйте еще раз (например: 45 65)")

@router.message(ProfileStates.waiting_for_rooms)
async def process_rooms(message: types.Message, state: FSMContext):
    raw_rooms = message.text.strip()
    rooms = []
    if "Студия" in raw_rooms:
        rooms.append(0)
        
    nums = re.findall(r'\d+', raw_rooms)
    rooms.extend([int(n) for n in nums])
    
    if not rooms and "Студия" not in raw_rooms:
         await message.answer("⚠️ Выберите вариант из меню или напишите цифры.")
         return

    await state.update_data(rooms=rooms)
    
    await message.answer(
        "Шаг 4. Минимальная площадь (м²)? (Просто число, например 35)\n"
        "Если не важно - напишите 0",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(ProfileStates.waiting_for_area)

@router.message(ProfileStates.waiting_for_area)
async def process_area(message: types.Message, state: FSMContext):
    try:
        area = int(message.text.strip())
        await state.update_data(area=area)
        
        await message.answer(
            "Шаг 5. Этаж:",
            reply_markup=profile_kb.get_floor_kb()
        )
        await state.set_state(ProfileStates.waiting_for_floor)
    except ValueError:
        await message.answer("Введите число (например, 30) или 0.")

@router.message(ProfileStates.waiting_for_floor)
async def process_floor(message: types.Message, state: FSMContext):
    floor_pref = message.text.strip()
    # Save as text for now, parse later or map to CIAN filter immediately?
    # Map for DB:
    # "Любой" -> None
    # "Не первый" -> min_floor=2
    # "Не последний" -> ? (Complex)
    # "Не первый и не последний" -> min=2, not_last=True
    await state.update_data(floor_pref=floor_pref)
    
    await message.answer(
        "Шаг 6. Ремонт:",
        reply_markup=profile_kb.get_renovation_kb()
    )
    await state.set_state(ProfileStates.waiting_for_renovation)

@router.message(ProfileStates.waiting_for_renovation)
async def process_renovation(message: types.Message, state: FSMContext):
    renovation = message.text.strip()
    await state.update_data(renovation=renovation)
    
    await message.answer(
        "Шаг 7. Придумайте название для этого профиля:",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(ProfileStates.waiting_for_alias)

@router.message(ProfileStates.waiting_for_alias)
async def process_alias(message: types.Message, state: FSMContext):
    alias = message.text.strip()
    data = await state.get_data()
    
    tg_user_id = message.from_user.id
    
    async with async_session() as session:
        result = await session.execute(select(User).where(User.tg_user_id == tg_user_id))
        user = result.scalar_one_or_none()
        
        if not user:
            await message.answer("Ошибка пользователя. Нажмите /start")
            return

        # Construct CIAN filter
        cian_filter = {
            "region_id": 1,
            "min_price": data['min_price'],
            "max_price": data['max_price'],
            "rooms": data['rooms'],
            "area_min": data.get('area'),
            "floor_pref": data.get('floor_pref'),
            "renovation": data.get('renovation')
        }

        new_profile = Profile(
            user_id=user.id,
            alias=alias,
            city=data['city'],
            cian_filter=cian_filter,
            weight_beauty=0.5,
            weight_price_quality=0.3,
            weight_distance=0.2
        )
        
        session.add(new_profile)
        try:
            await session.commit()
            await message.answer(
                f"✅ Профиль <b>{alias}</b> создан!\n\n"
                f"Фильтры: {data['city']}, {data['min_price']}-{data['max_price']} ₽\n"
                f"Комнаты: {data['rooms']}, Площадь: >{data['area']}м²\n"
                f"Этаж: {data['floor_pref']}, Ремонт: {data['renovation']}\n\n"
                "Теперь жми /next, чтобы смотреть квартиры!"
            )
        except Exception as e:
            await session.rollback()
            await message.answer(f"Ошибка при сохранении: {e}")
            
    await state.clear()
