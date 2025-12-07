from datetime import datetime
from typing import Optional
from sqlalchemy import Numeric, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base

class ProfileFlatScore(Base):
    __tablename__ = "profile_flat_score"

    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), primary_key=True)
    flat_id: Mapped[int] = mapped_column(ForeignKey("flats.id"), primary_key=True)
    score: Mapped[float] = mapped_column(Numeric(8, 5), nullable=False)
    beauty_hat: Mapped[Optional[float]] = mapped_column(Numeric(6, 4))
    price_quality_hat: Mapped[Optional[float]] = mapped_column(Numeric(6, 4))
    distance_hat: Mapped[Optional[float]] = mapped_column(Numeric(6, 4))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

