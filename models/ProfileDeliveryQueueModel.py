from datetime import datetime
from sqlalchemy import (
    BigInteger, Text, DateTime, ForeignKey,
    UniqueConstraint, func
)
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base

class ProfileDeliveryQueue(Base):
    __tablename__ = "profile_delivery_queue"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), nullable=False)
    flat_id: Mapped[int] = mapped_column(ForeignKey("flats.id"), nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, default='queued')
    
    enqueued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("profile_id", "flat_id", name="uix_delivery_queue"),
    )

