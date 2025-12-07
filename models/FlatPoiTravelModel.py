from datetime import datetime
from typing import Optional
from sqlalchemy import Integer, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base

class FlatPoiTravel(Base):
    __tablename__ = "flat_poi_travel"

    flat_id: Mapped[int] = mapped_column(ForeignKey("flats.id"), primary_key=True)
    poi_id: Mapped[int] = mapped_column(ForeignKey("pois.id"), primary_key=True)
    mode: Mapped[str] = mapped_column(Text, primary_key=True, default='masstransit')
    
    travel_min: Mapped[Optional[int]] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

