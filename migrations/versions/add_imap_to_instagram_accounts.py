"""Add imap_server and imap_port to instagram_accounts.

Revision ID: add_imap_to_instagram_accounts
Revises: add_tiktok_support
Create Date: 2026-04-07
"""
from alembic import op
import sqlalchemy as sa

revision = 'add_imap_to_instagram_accounts'
down_revision = 'add_tiktok_support'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('instagram_accounts') as batch_op:
        batch_op.add_column(sa.Column('imap_server', sa.String(120), nullable=True))
        batch_op.add_column(sa.Column('imap_port', sa.Integer(), server_default='993', nullable=True))


def downgrade():
    with op.batch_alter_table('instagram_accounts') as batch_op:
        batch_op.drop_column('imap_port')
        batch_op.drop_column('imap_server')
