from datetime import datetime
from typing import List, TYPE_CHECKING
from sqlalchemy import (
    BigInteger, Text, Numeric, DateTime, ForeignKey,
    UniqueConstraint, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from models.base import Base

if TYPE_CHECKING:
    from models.UserModel import User
    from models.ProfilePOIModel import ProfilePOI

class POI(Base):
    __tablename__ = "pois"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    lat: Mapped[float] = mapped_column(Numeric(10, 7), nullable=False)
    lng: Mapped[float] = mapped_column(Numeric(10, 7), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "label", name="uix_poi_user_label"),
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="pois")
    profile_associations: Mapped[List["ProfilePOI"]] = relationship("ProfilePOI", back_populates="poi")

