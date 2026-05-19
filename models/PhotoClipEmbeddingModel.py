from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, Integer, Text, DateTime, ForeignKey, LargeBinary, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base

if TYPE_CHECKING:
    from models.FlatPhotoModel import FlatPhoto


class PhotoClipEmbedding(Base):
    """
    CLIP (or compatible) image embedding per photo.
    Separate from PhotoEmbedding rows used as «downloaded to S3» markers (model=raw_image).
    """

    __tablename__ = "photo_clip_embeddings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    photo_id: Mapped[int] = mapped_column(ForeignKey("flat_photos.id"), nullable=False, unique=True)
    flat_id: Mapped[Optional[int]] = mapped_column(ForeignKey("flats.id"), nullable=True, index=True)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    dim: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[bytes] = mapped_column(LargeBinary(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    photo: Mapped["FlatPhoto"] = relationship("FlatPhoto", back_populates="clip_embedding")
