from datetime import datetime
from typing import Optional
from sqlalchemy import (
    BigInteger, Integer, Text, Boolean, DateTime, ForeignKey,
    UniqueConstraint, Index, func
)
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base

class Rating(Base):
    __tablename__ = "ratings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), nullable=False)
    flat_id: Mapped[int] = mapped_column(ForeignKey("flats.id"), nullable=False)
    
    beauty: Mapped[Optional[int]] = mapped_column(Integer)
    price_quality: Mapped[Optional[int]] = mapped_column(Integer)
    distance_pref: Mapped[Optional[int]] = mapped_column(Integer)
    
    skipped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(Text, nullable=False, default='telegram')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "profile_id", "flat_id", name="uix_rating_user_profile_flat"),
        Index("idx_rating_profile_created", "profile_id", "created_at"),
    )

