from datetime import datetime
from typing import Optional, List, TYPE_CHECKING
from sqlalchemy import (
    BigInteger, Text, Boolean, Numeric, DateTime, ForeignKey,
    UniqueConstraint, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import JSONB
from models.base import Base

if TYPE_CHECKING:
    from models.UserModel import User
    from models.ProfilePOIModel import ProfilePOI
    from models.ModelSnapshotModel import ModelSnapshot

class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    city: Mapped[str] = mapped_column(Text, nullable=False)
    cian_filter: Mapped[dict] = mapped_column(JSONB, nullable=False)
    
    weight_beauty: Mapped[float] = mapped_column(Numeric(5, 3), nullable=False, default=0.50)
    weight_price_quality: Mapped[float] = mapped_column(Numeric(5, 3), nullable=False, default=0.30)
    weight_distance: Mapped[float] = mapped_column(Numeric(5, 3), nullable=False, default=0.20)
    epsilon_explore: Mapped[float] = mapped_column(Numeric(5, 3), nullable=False, default=0.20)
    
    stage: Mapped[str] = mapped_column(Text, nullable=False, default='single') # 'single' | 'pairwise'
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    public_slug: Mapped[Optional[str]] = mapped_column(Text, unique=True)
    
    forked_from_profile_id: Mapped[Optional[int]] = mapped_column(ForeignKey("profiles.id"))
    last_trained_snapshot_id: Mapped[Optional[int]] = mapped_column(ForeignKey("model_snapshots.id"))
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "alias", name="uix_profile_user_alias"),
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="profiles")
    pois: Mapped[List["ProfilePOI"]] = relationship("ProfilePOI", back_populates="profile")
    snapshots: Mapped[List["ModelSnapshot"]] = relationship("ModelSnapshot", back_populates="profile", foreign_keys="[ModelSnapshot.profile_id]")

