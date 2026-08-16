"""add retention days to companies

Revision ID: z9x8w7v6u5t4
Revises: p1r2o3d4u5c6
Create Date: 2026-08-10

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'z9x8w7v6u5t4'
down_revision = 'p1r2o3d4u5c6'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS history_retention_days INTEGER DEFAULT NULL")
    op.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS history_archive_retention_days INTEGER DEFAULT NULL")

def downgrade() -> None:
    pass
