from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

def get_city_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Москва")]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_rooms_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="1"), KeyboardButton(text="2")],
            [KeyboardButton(text="1, 2"), KeyboardButton(text="2, 3")],
            [KeyboardButton(text="Студия")]
        ],
        resize_keyboard=True
    )

def get_floor_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Любой")],
            [KeyboardButton(text="Не первый"), KeyboardButton(text="Не последний")],
            [KeyboardButton(text="Не первый и не последний")]
        ],
        resize_keyboard=True
    )

def get_renovation_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Любой")],
            [KeyboardButton(text="Косметический+"), KeyboardButton(text="Евро+")],
            [KeyboardButton(text="Дизайнерский")]
        ],
        resize_keyboard=True
    )
