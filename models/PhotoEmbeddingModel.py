from datetime import datetime
from typing import TYPE_CHECKING, Optional
from sqlalchemy import (
    BigInteger, Integer, Text, DateTime, ForeignKey, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from models.base import Base

if TYPE_CHECKING:
    from models.FlatPhotoModel import FlatPhoto

class PhotoEmbedding(Base):
    __tablename__ = "photo_embeddings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    photo_id: Mapped[int] = mapped_column(ForeignKey("flat_photos.id"), nullable=False, unique=True)
    flat_id: Mapped[Optional[int]] = mapped_column(ForeignKey("flats.id"), nullable=True, index=True)
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False, default='CLIP-ViT-B/32')
    dim: Mapped[int] = mapped_column(Integer, nullable=False, default=512)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    photo: Mapped["FlatPhoto"] = relationship("FlatPhoto", back_populates="embedding")

