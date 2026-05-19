from typing import List

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def kb_add_mode() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🚌 Общ. транспорт", callback_data="pam:masstransit"),
                InlineKeyboardButton(text="🚶 Пешком", callback_data="pam:pedestrian"),
            ],
        ]
    )


def kb_poi_mode_choice(poi_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🚌 Общ. транспорт", callback_data=f"pmod:{poi_id}:masstransit"),
                InlineKeyboardButton(text="🚶 Пешком", callback_data=f"pmod:{poi_id}:pedestrian"),
            ],
        ]
    )


def kb_poi_actions_rows(poi_id: int) -> List[List[InlineKeyboardButton]]:
    """Две строки кнопок: правки + имя/удаление (чтобы не перегружать одну строку)."""
    return [
        [
            InlineKeyboardButton(text="⏱ макс", callback_data=f"pxm:{poi_id}"),
            InlineKeyboardButton(text="⚡ приор", callback_data=f"pxp:{poi_id}"),
            InlineKeyboardButton(text="🚌 режим", callback_data=f"pxr:{poi_id}"),
        ],
        [
            InlineKeyboardButton(text="✏️ имя", callback_data=f"pxn:{poi_id}"),
            InlineKeyboardButton(text="🗑", callback_data=f"pxd:{poi_id}"),
        ],
    ]
