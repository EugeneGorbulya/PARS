from datetime import datetime
from typing import Optional
from sqlalchemy import Integer, Numeric, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base

class ProfileMetrics(Base):
    __tablename__ = "profile_metrics"

    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), primary_key=True)
    ratings_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pairwise_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stability_tau: Mapped[Optional[float]] = mapped_column(Numeric(5, 3))
    last_trained_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

