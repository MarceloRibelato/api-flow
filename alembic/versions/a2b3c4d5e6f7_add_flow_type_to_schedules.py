"""add flow_type to schedules

Revision ID: a2b3c4d5e6f7
Revises: f3f4e5d6c7b8
Create Date: 2026-03-06 17:20:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'a2b3c4d5e6f7'
down_revision = ('f3f4e5d6c7b8', 'c1d2e3f4g5h6')
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('schedules', sa.Column('flow_type', sa.String(20), nullable=True, server_default='api'))


def downgrade():
    op.drop_column('schedules', 'flow_type')
