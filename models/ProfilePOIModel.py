from typing import TYPE_CHECKING
from sqlalchemy import Integer, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from models.base import Base

if TYPE_CHECKING:
    from models.ProfileModel import Profile
    from models.POIModel import POI

class ProfilePOI(Base):
    __tablename__ = "profile_pois"

    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), primary_key=True)
    poi_id: Mapped[int] = mapped_column(ForeignKey("pois.id"), primary_key=True)
    max_travel_min: Mapped[int] = mapped_column(Integer, nullable=False, default=45)
    mode: Mapped[str] = mapped_column(Text, nullable=False, default='masstransit')
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # Relationships
    profile: Mapped["Profile"] = relationship("Profile", back_populates="pois")
    poi: Mapped["POI"] = relationship("POI", back_populates="profile_associations")

