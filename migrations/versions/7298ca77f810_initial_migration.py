"""Initial migration

Revision ID: 7298ca77f810
Revises: 
Create Date: 2025-11-30 11:26:30.287286

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '7298ca77f810'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Independent tables
    op.create_table('users',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('tg_user_id', sa.BigInteger(), nullable=True),
    sa.Column('username', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_tg_user_id'), 'users', ['tg_user_id'], unique=True)

    op.create_table('flats',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('cian_id', sa.BigInteger(), nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('city', sa.Text(), nullable=False),
    sa.Column('address', sa.Text(), nullable=True),
    sa.Column('lat', sa.Numeric(precision=10, scale=7), nullable=True),
    sa.Column('lng', sa.Numeric(precision=10, scale=7), nullable=True),
    sa.Column('price_rub', sa.Integer(), nullable=True),
    sa.Column('rooms', sa.Integer(), nullable=True),
    sa.Column('area_sqm', sa.Numeric(precision=7, scale=2), nullable=True),
    sa.Column('floor', sa.Integer(), nullable=True),
    sa.Column('floors_total', sa.Integer(), nullable=True),
    sa.Column('building_year', sa.Integer(), nullable=True),
    sa.Column('material', sa.Text(), nullable=True),
    sa.Column('nearest_metro', sa.Text(), nullable=True),
    sa.Column('metro_distance_m', sa.Integer(), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('fetched_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('deactivated_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('cian_id')
    )
    op.create_index('idx_flats_active', 'flats', ['active'], unique=False)
    op.create_index('idx_flats_city_published', 'flats', ['city', 'published_at'], unique=False)

    # 2. Profiles (depends on users). Circular dependency with model_snapshots resolved later.
    op.create_table('profiles',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('alias', sa.Text(), nullable=False),
    sa.Column('city', sa.Text(), nullable=False),
    sa.Column('cian_filter', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('weight_beauty', sa.Numeric(precision=5, scale=3), nullable=False),
    sa.Column('weight_price_quality', sa.Numeric(precision=5, scale=3), nullable=False),
    sa.Column('weight_distance', sa.Numeric(precision=5, scale=3), nullable=False),
    sa.Column('epsilon_explore', sa.Numeric(precision=5, scale=3), nullable=False),
    sa.Column('stage', sa.Text(), nullable=False),
    sa.Column('is_public', sa.Boolean(), nullable=False),
    sa.Column('public_slug', sa.Text(), nullable=True),
    sa.Column('forked_from_profile_id', sa.BigInteger(), nullable=True),
    sa.Column('last_trained_snapshot_id', sa.BigInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['forked_from_profile_id'], ['profiles.id'], ),
    # FK to model_snapshots removed here to avoid circular dependency error
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('public_slug'),
    sa.UniqueConstraint('user_id', 'alias', name='uix_profile_user_alias')
    )

    # 3. Model Snapshots (depends on profiles)
    op.create_table('model_snapshots',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('profile_id', sa.BigInteger(), nullable=False),
    sa.Column('backbone', sa.Text(), nullable=False),
    sa.Column('head_type', sa.Text(), nullable=False),
    sa.Column('storage_uri', sa.Text(), nullable=False),
    sa.Column('metrics', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('kendall_tau_top20', sa.Numeric(precision=5, scale=3), nullable=True),
    sa.Column('mae', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['profile_id'], ['profiles.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_snapshots_profile_created', 'model_snapshots', ['profile_id', 'created_at'], unique=False)

    # 4. Add circular FK back to profiles
    op.create_foreign_key(
        'fk_profiles_last_trained_snapshot_id_model_snapshots',
        'profiles', 'model_snapshots',
        ['last_trained_snapshot_id'], ['id']
    )

    # 5. Other tables (depend on users, profiles, flats)
    op.create_table('flat_photos',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('flat_id', sa.BigInteger(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('room_type', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['flat_id'], ['flats.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('flat_id', 'seq', name='uix_flat_photo_seq')
    )
    op.create_table('hidden_flats',
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('profile_id', sa.BigInteger(), nullable=False),
    sa.Column('flat_id', sa.BigInteger(), nullable=False),
    sa.Column('hidden_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['flat_id'], ['flats.id'], ),
    sa.ForeignKeyConstraint(['profile_id'], ['profiles.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('user_id', 'profile_id', 'flat_id')
    )
    op.create_table('pairwise_ratings',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('profile_id', sa.BigInteger(), nullable=False),
    sa.Column('flat_a_id', sa.BigInteger(), nullable=False),
    sa.Column('flat_b_id', sa.BigInteger(), nullable=False),
    sa.Column('factor', sa.Text(), nullable=False),
    sa.Column('preferred_flat_id', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['flat_a_id'], ['flats.id'], ),
    sa.ForeignKeyConstraint(['flat_b_id'], ['flats.id'], ),
    sa.ForeignKeyConstraint(['preferred_flat_id'], ['flats.id'], ),
    sa.ForeignKeyConstraint(['profile_id'], ['profiles.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'profile_id', 'flat_a_id', 'flat_b_id', 'factor', name='uix_pairwise')
    )
    op.create_index('idx_pairwise_profile_created', 'pairwise_ratings', ['profile_id', 'created_at'], unique=False)
    op.create_table('pois',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('label', sa.Text(), nullable=False),
    sa.Column('lat', sa.Numeric(precision=10, scale=7), nullable=False),
    sa.Column('lng', sa.Numeric(precision=10, scale=7), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'label', name='uix_poi_user_label')
    )
    op.create_table('profile_delivery_queue',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('profile_id', sa.BigInteger(), nullable=False),
    sa.Column('flat_id', sa.BigInteger(), nullable=False),
    sa.Column('state', sa.Text(), nullable=False),
    sa.Column('enqueued_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['flat_id'], ['flats.id'], ),
    sa.ForeignKeyConstraint(['profile_id'], ['profiles.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('profile_id', 'flat_id', name='uix_delivery_queue')
    )
    op.create_table('profile_flat_score',
    sa.Column('profile_id', sa.BigInteger(), nullable=False),
    sa.Column('flat_id', sa.BigInteger(), nullable=False),
    sa.Column('score', sa.Numeric(precision=8, scale=5), nullable=False),
    sa.Column('beauty_hat', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('price_quality_hat', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('distance_hat', sa.Numeric(precision=6, scale=4), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['flat_id'], ['flats.id'], ),
    sa.ForeignKeyConstraint(['profile_id'], ['profiles.id'], ),
    sa.PrimaryKeyConstraint('profile_id', 'flat_id')
    )
    op.create_table('profile_metrics',
    sa.Column('profile_id', sa.BigInteger(), nullable=False),
    sa.Column('ratings_count', sa.Integer(), nullable=False),
    sa.Column('pairwise_count', sa.Integer(), nullable=False),
    sa.Column('stability_tau', sa.Numeric(precision=5, scale=3), nullable=True),
    sa.Column('last_trained_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['profile_id'], ['profiles.id'], ),
    sa.PrimaryKeyConstraint('profile_id')
    )
    op.create_table('ratings',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('profile_id', sa.BigInteger(), nullable=False),
    sa.Column('flat_id', sa.BigInteger(), nullable=False),
    sa.Column('beauty', sa.Integer(), nullable=True),
    sa.Column('price_quality', sa.Integer(), nullable=True),
    sa.Column('distance_pref', sa.Integer(), nullable=True),
    sa.Column('skipped', sa.Boolean(), nullable=False),
    sa.Column('source', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['flat_id'], ['flats.id'], ),
    sa.ForeignKeyConstraint(['profile_id'], ['profiles.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'profile_id', 'flat_id', name='uix_rating_user_profile_flat')
    )
    op.create_index('idx_rating_profile_created', 'ratings', ['profile_id', 'created_at'], unique=False)
    op.create_table('saved_flats',
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('profile_id', sa.BigInteger(), nullable=False),
    sa.Column('flat_id', sa.BigInteger(), nullable=False),
    sa.Column('saved_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['flat_id'], ['flats.id'], ),
    sa.ForeignKeyConstraint(['profile_id'], ['profiles.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('user_id', 'profile_id', 'flat_id')
    )
    op.create_table('seen_flats',
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('profile_id', sa.BigInteger(), nullable=False),
    sa.Column('flat_id', sa.BigInteger(), nullable=False),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['flat_id'], ['flats.id'], ),
    sa.ForeignKeyConstraint(['profile_id'], ['profiles.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('user_id', 'profile_id', 'flat_id')
    )
    op.create_table('flat_poi_travel',
    sa.Column('flat_id', sa.BigInteger(), nullable=False),
    sa.Column('poi_id', sa.BigInteger(), nullable=False),
    sa.Column('mode', sa.Text(), nullable=False),
    sa.Column('travel_min', sa.Integer(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['flat_id'], ['flats.id'], ),
    sa.ForeignKeyConstraint(['poi_id'], ['pois.id'], ),
    sa.PrimaryKeyConstraint('flat_id', 'poi_id', 'mode')
    )
    op.create_table('photo_embeddings',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('photo_id', sa.BigInteger(), nullable=False),
    sa.Column('storage_uri', sa.Text(), nullable=False),
    sa.Column('model', sa.Text(), nullable=False),
    sa.Column('dim', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['photo_id'], ['flat_photos.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('photo_id')
    )
    op.create_table('profile_pois',
    sa.Column('profile_id', sa.BigInteger(), nullable=False),
    sa.Column('poi_id', sa.BigInteger(), nullable=False),
    sa.Column('max_travel_min', sa.Integer(), nullable=False),
    sa.Column('mode', sa.Text(), nullable=False),
    sa.Column('priority', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['poi_id'], ['pois.id'], ),
    sa.ForeignKeyConstraint(['profile_id'], ['profiles.id'], ),
    sa.PrimaryKeyConstraint('profile_id', 'poi_id')
    )


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('profile_pois')
    op.drop_table('photo_embeddings')
    op.drop_table('flat_poi_travel')
    op.drop_table('seen_flats')
    op.drop_table('saved_flats')
    op.drop_index('idx_rating_profile_created', table_name='ratings')
    op.drop_table('ratings')
    op.drop_table('profile_metrics')
    op.drop_table('profile_flat_score')
    op.drop_table('profile_delivery_queue')
    op.drop_table('pois')
    op.drop_index('idx_pairwise_profile_created', table_name='pairwise_ratings')
    op.drop_table('pairwise_ratings')
    op.drop_table('hidden_flats')
    op.drop_table('flat_photos')
    op.drop_index(op.f('ix_users_tg_user_id'), table_name='users')
    
    # Remove circular FK before dropping tables
    op.drop_constraint('fk_profiles_last_trained_snapshot_id_model_snapshots', 'profiles', type_='foreignkey')
    
    op.drop_table('model_snapshots') # Now safe to drop
    op.drop_table('profiles')
    op.drop_table('users')
    op.drop_index('idx_flats_city_published', table_name='flats')
    op.drop_index('idx_flats_active', table_name='flats')
    op.drop_table('flats')
    # ### end Alembic commands ###
