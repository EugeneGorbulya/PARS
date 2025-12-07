from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def get_rate_kb(flat_id: int, metric: str = "beauty"):
    """
    metric: 'beauty' | 'price' | 'dist'
    """
    # Callback format: rate:score:flat_id:metric
    kb = [
        [
            InlineKeyboardButton(text="❤️ 5", callback_data=f"rate:5:{flat_id}:{metric}"),
            InlineKeyboardButton(text="👍 4", callback_data=f"rate:4:{flat_id}:{metric}"),
            InlineKeyboardButton(text="😐 3", callback_data=f"rate:3:{flat_id}:{metric}"),
        ],
        [
            InlineKeyboardButton(text="👎 2", callback_data=f"rate:2:{flat_id}:{metric}"),
            InlineKeyboardButton(text="🤢 1", callback_data=f"rate:1:{flat_id}:{metric}"),
        ],
        [
            InlineKeyboardButton(text="➡️ Пропустить (Скрыть)", callback_data=f"rate:skip:{flat_id}:{metric}")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)
