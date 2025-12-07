from datetime import datetime
from typing import Optional, List, TYPE_CHECKING
from sqlalchemy import (
    BigInteger, Integer, Text, Boolean, Numeric, DateTime,
    Index, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from models.base import Base

if TYPE_CHECKING:
    from models.FlatPhotoModel import FlatPhoto

class Flat(Base):
    __tablename__ = "flats"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cian_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    city: Mapped[str] = mapped_column(Text, nullable=False)
    address: Mapped[Optional[str]] = mapped_column(Text)
    lat: Mapped[Optional[float]] = mapped_column(Numeric(10, 7))
    lng: Mapped[Optional[float]] = mapped_column(Numeric(10, 7))
    
    price_rub: Mapped[Optional[int]] = mapped_column(Integer)
    rooms: Mapped[Optional[int]] = mapped_column(Integer)
    area_sqm: Mapped[Optional[float]] = mapped_column(Numeric(7, 2))
    floor: Mapped[Optional[int]] = mapped_column(Integer)
    floors_total: Mapped[Optional[int]] = mapped_column(Integer)
    building_year: Mapped[Optional[int]] = mapped_column(Integer)
    material: Mapped[Optional[str]] = mapped_column(Text)
    nearest_metro: Mapped[Optional[str]] = mapped_column(Text)
    metro_distance_m: Mapped[Optional[int]] = mapped_column(Integer)
    
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    deactivated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("idx_flats_city_published", "city", "published_at"),
        Index("idx_flats_active", "active"),
    )

    # Relationships
    photos: Mapped[List["FlatPhoto"]] = relationship("FlatPhoto", back_populates="flat")

