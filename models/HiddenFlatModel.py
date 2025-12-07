from datetime import datetime
from sqlalchemy import BigInteger, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base

class HiddenFlat(Base):
    __tablename__ = "hidden_flats"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), primary_key=True)
    flat_id: Mapped[int] = mapped_column(ForeignKey("flats.id"), primary_key=True)
    hidden_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

