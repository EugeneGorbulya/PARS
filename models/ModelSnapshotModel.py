from datetime import datetime
from typing import Optional, TYPE_CHECKING
from sqlalchemy import (
    BigInteger, Text, Numeric, DateTime, ForeignKey,
    Index, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import JSONB
from models.base import Base

if TYPE_CHECKING:
    from models.ProfileModel import Profile

class ModelSnapshot(Base):
    __tablename__ = "model_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), nullable=False)
    backbone: Mapped[str] = mapped_column(Text, nullable=False, default='CLIP-ViT-B/32')
    head_type: Mapped[str] = mapped_column(Text, nullable=False, default='MLP')
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    metrics: Mapped[Optional[dict]] = mapped_column(JSONB)
    kendall_tau_top20: Mapped[Optional[float]] = mapped_column(Numeric(5, 3))
    mae: Mapped[Optional[float]] = mapped_column(Numeric(6, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_snapshots_profile_created", "profile_id", "created_at"),
    )
    
    # Relationships
    profile: Mapped["Profile"] = relationship("Profile", back_populates="snapshots", foreign_keys=[profile_id])

