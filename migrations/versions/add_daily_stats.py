"""add daily stats columns

Revision ID: add_daily_stats
Revises: 
Create Date: 2024-01-30 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime


# revision identifiers, used by Alembic.
revision = 'add_daily_stats'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Add daily statistics columns
    op.add_column('instagram_accounts', sa.Column('daily_likes', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('instagram_accounts', sa.Column('daily_comments', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('instagram_accounts', sa.Column('daily_follows', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('instagram_accounts', sa.Column('daily_unfollows', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('instagram_accounts', sa.Column('daily_stories_viewed', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('instagram_accounts', sa.Column('last_reset_date', sa.Date(), nullable=True))

    # Set default last_reset_date to current date for existing records
    op.execute("UPDATE instagram_accounts SET last_reset_date = CURRENT_DATE WHERE last_reset_date IS NULL")


def downgrade():
    # Remove daily statistics columns
    op.drop_column('instagram_accounts', 'daily_likes')
    op.drop_column('instagram_accounts', 'daily_comments')
    op.drop_column('instagram_accounts', 'daily_follows')
    op.drop_column('instagram_accounts', 'daily_unfollows')
    op.drop_column('instagram_accounts', 'daily_stories_viewed')
    op.drop_column('instagram_accounts', 'last_reset_date') 