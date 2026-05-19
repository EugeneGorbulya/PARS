"""add flat_id to photo_embeddings

Revision ID: add_flat_id_pe
Revises: 7298ca77f810
Create Date: 2025-02-22

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "add_flat_id_pe"
down_revision = "7298ca77f810"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "photo_embeddings",
        sa.Column("flat_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_photo_embeddings_flat_id_flats",
        "photo_embeddings",
        "flats",
        ["flat_id"],
        ["id"],
    )
    op.create_index(
        "ix_photo_embeddings_flat_id",
        "photo_embeddings",
        ["flat_id"],
        unique=False,
    )
    # Backfill from flat_photos
    op.execute("""
        UPDATE photo_embeddings pe
        SET flat_id = fp.flat_id
        FROM flat_photos fp
        WHERE pe.photo_id = fp.id
    """)


def downgrade() -> None:
    op.drop_index("ix_photo_embeddings_flat_id", table_name="photo_embeddings")
    op.drop_constraint(
        "fk_photo_embeddings_flat_id_flats",
        "photo_embeddings",
        type_="foreignkey",
    )
    op.drop_column("photo_embeddings", "flat_id")
