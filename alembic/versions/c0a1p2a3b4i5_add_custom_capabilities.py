"""add custom_capabilities to product_mobile_settings and product_mobile_devices

Revision ID: c0a1p2a3b4i5
Revises: z9z9z9z9z9za
Create Date: 2026-08-20 07:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'c0a1p2a3b4i5'
down_revision = 'z9z9z9z9z9za'
branch_labels = None
depends_on = None

def upgrade():
    op.execute("ALTER TABLE product_mobile_settings ADD COLUMN IF NOT EXISTS custom_capabilities TEXT")
    op.execute("ALTER TABLE product_mobile_devices ADD COLUMN IF NOT EXISTS custom_capabilities TEXT")

def downgrade():
    op.execute("ALTER TABLE product_mobile_settings DROP COLUMN IF EXISTS custom_capabilities")
    op.execute("ALTER TABLE product_mobile_devices DROP COLUMN IF EXISTS custom_capabilities")
