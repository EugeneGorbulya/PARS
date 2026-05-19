"""photo_clip_embeddings table for CLIP vectors (separate from raw_image marker)

Revision ID: photo_clip_emb_01
Revises: add_flat_id_pe
Create Date: 2026-05-09

"""
from alembic import op
import sqlalchemy as sa

revision = "photo_clip_emb_01"
down_revision = "add_flat_id_pe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "photo_clip_embeddings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("photo_id", sa.BigInteger(), nullable=False),
        sa.Column("flat_id", sa.BigInteger(), nullable=True),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=False),
        sa.Column("embedding", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["flat_id"], ["flats.id"], name="fk_photo_clip_emb_flat_id"),
        sa.ForeignKeyConstraint(["photo_id"], ["flat_photos.id"], name="fk_photo_clip_emb_photo_id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("photo_id", name="uix_photo_clip_emb_photo_id"),
    )
    op.create_index(
        "ix_photo_clip_embeddings_flat_id",
        "photo_clip_embeddings",
        ["flat_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_photo_clip_embeddings_flat_id", table_name="photo_clip_embeddings")
    op.drop_table("photo_clip_embeddings")
