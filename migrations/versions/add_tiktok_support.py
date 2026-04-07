"""Add platform column to devices and create tiktok_accounts table.

Revision ID: add_tiktok_support
Revises: 42e0a4a097f4
Create Date: 2026-04-07
"""
from alembic import op
import sqlalchemy as sa

revision = 'add_tiktok_support'
down_revision = '42e0a4a097f4'
branch_labels = None
depends_on = None


def upgrade():
    # Add platform column to existing devices table
    op.add_column(
        'devices',
        sa.Column('platform', sa.String(16), nullable=False, server_default='instagram'),
    )

    # Create tiktok_accounts table
    op.create_table(
        'tiktok_accounts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('username', sa.String(80), unique=True, nullable=False),
        sa.Column('password', sa.String(120)),
        sa.Column('device_id', sa.String(120),
                  sa.ForeignKey('devices.device_id', ondelete='SET NULL'), nullable=True),
        sa.Column('login_status', sa.Boolean(), default=False),
        sa.Column('email', sa.String(120)),
        sa.Column('email_password', sa.String(120)),
        sa.Column('last_login', sa.DateTime()),
        sa.Column('last_logout', sa.DateTime()),
        sa.Column('error_message', sa.Text()),
        sa.Column('created_at', sa.DateTime()),
        sa.Column('updated_at', sa.DateTime()),
        # Lifetime stats
        sa.Column('total_likes', sa.Integer(), default=0),
        sa.Column('total_follows', sa.Integer(), default=0),
        sa.Column('total_unfollows', sa.Integer(), default=0),
        sa.Column('total_profile_views', sa.Integer(), default=0),
        sa.Column('total_video_views', sa.Integer(), default=0),
        sa.Column('total_comments', sa.Integer(), default=0),
        # Daily stats
        sa.Column('daily_likes', sa.Integer(), default=0),
        sa.Column('daily_follows', sa.Integer(), default=0),
        sa.Column('daily_unfollows', sa.Integer(), default=0),
        sa.Column('daily_profile_views', sa.Integer(), default=0),
        sa.Column('daily_video_views', sa.Integer(), default=0),
        sa.Column('daily_comments', sa.Integer(), default=0),
        sa.Column('last_daily_reset', sa.DateTime()),
    )


def downgrade():
    op.drop_table('tiktok_accounts')
    op.drop_column('devices', 'platform')
