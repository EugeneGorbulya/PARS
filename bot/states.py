from aiogram.fsm.state import StatesGroup, State

class ProfileStates(StatesGroup):
    waiting_for_city = State()
    waiting_for_price = State()
    waiting_for_rooms = State()
    waiting_for_area = State()
    waiting_for_floor = State()
    waiting_for_renovation = State()
    waiting_for_foot_min = State()      # время до метро, мин
    waiting_for_min_house_year = State() # год постройки не раньше
    waiting_for_alias = State()


class PoiStates(StatesGroup):
    """Мастер добавления точки; edit_numeric / edit_label — правки полей."""

    add_label = State()
    add_location = State()
    add_max_travel = State()
    add_priority = State()
    add_mode = State()
    edit_numeric = State()
    edit_label = State()
