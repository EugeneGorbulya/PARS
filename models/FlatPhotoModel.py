from datetime import datetime
from typing import Optional, TYPE_CHECKING
from sqlalchemy import (
    BigInteger, Integer, Text, DateTime, ForeignKey,
    UniqueConstraint, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from models.base import Base

if TYPE_CHECKING:
    from models.FlatModel import Flat
    from models.PhotoEmbeddingModel import PhotoEmbedding
    from models.PhotoClipEmbeddingModel import PhotoClipEmbedding

class FlatPhoto(Base):
    __tablename__ = "flat_photos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    flat_id: Mapped[int] = mapped_column(ForeignKey("flats.id"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    room_type: Mapped[Optional[str]] = mapped_column(Text) # 'kitchen','bathroom' etc.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("flat_id", "seq", name="uix_flat_photo_seq"),
    )

    # Relationships
    flat: Mapped["Flat"] = relationship("Flat", back_populates="photos")
    embedding: Mapped[Optional["PhotoEmbedding"]] = relationship("PhotoEmbedding", back_populates="photo", uselist=False)
    clip_embedding: Mapped[Optional["PhotoClipEmbedding"]] = relationship(
        "PhotoClipEmbedding", back_populates="photo", uselist=False
    )

