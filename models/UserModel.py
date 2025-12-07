from datetime import datetime
from typing import Optional, List, TYPE_CHECKING
from sqlalchemy import BigInteger, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from models.base import Base

if TYPE_CHECKING:
    from models.ProfileModel import Profile
    from models.POIModel import POI

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tg_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    profiles: Mapped[List["Profile"]] = relationship("Profile", back_populates="user")
    pois: Mapped[List["POI"]] = relationship("POI", back_populates="user")

