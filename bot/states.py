from aiogram.fsm.state import StatesGroup, State

class ProfileStates(StatesGroup):
    waiting_for_city = State()
    waiting_for_price = State()
    waiting_for_rooms = State()
    waiting_for_area = State()
    waiting_for_floor = State()
    waiting_for_renovation = State()
    waiting_for_alias = State()
