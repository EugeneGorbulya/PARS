from datetime import datetime
from sqlalchemy import (
    BigInteger, Text, DateTime, ForeignKey,
    UniqueConstraint, Index, func
)
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base

class PairwiseRating(Base):
    __tablename__ = "pairwise_ratings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), nullable=False)
    flat_a_id: Mapped[int] = mapped_column(ForeignKey("flats.id"), nullable=False)
    flat_b_id: Mapped[int] = mapped_column(ForeignKey("flats.id"), nullable=False)
    factor: Mapped[str] = mapped_column(Text, nullable=False) # 'beauty', etc
    preferred_flat_id: Mapped[int] = mapped_column(ForeignKey("flats.id"), nullable=False)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "profile_id", "flat_a_id", "flat_b_id", "factor", name="uix_pairwise"),
        Index("idx_pairwise_profile_created", "profile_id", "created_at"),
    )

