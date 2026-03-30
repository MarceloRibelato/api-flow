"""add product_mobile_settings and product_mobile_devices tables

Revision ID: p1r2o3d4u5c6
Revises: m1g2r3a4t5e6
Create Date: 2026-03-30

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

# revision identifiers, used by Alembic.
revision = 'p1r2o3d4u5c6'
down_revision = 'm1g2r3a4t5e6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = Inspector.from_engine(conn)
    existing_tables = inspector.get_table_names()

    # Create product_mobile_settings if not exists
    if 'product_mobile_settings' not in existing_tables:
        op.create_table(
            'product_mobile_settings',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('product_id', sa.Integer(), sa.ForeignKey('products.id'), nullable=False),
            sa.Column('provider', sa.String(length=50), nullable=True, server_default='appium_local'),
            sa.Column('server_url', sa.String(length=255), nullable=True),
            sa.Column('auth_user', sa.String(length=100), nullable=True),
            sa.Column('auth_token', sa.String(length=255), nullable=True),
            sa.Column('device_name', sa.String(length=100), nullable=True),
            sa.Column('platform_version', sa.String(length=50), nullable=True),
            sa.Column('app_identifier', sa.String(length=255), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('product_id'),
        )
        op.create_index(
            op.f('ix_product_mobile_settings_id'),
            'product_mobile_settings', ['id'], unique=False
        )

    # Create product_mobile_devices if not exists
    if 'product_mobile_devices' not in existing_tables:
        op.create_table(
            'product_mobile_devices',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('product_id', sa.Integer(), sa.ForeignKey('products.id'), nullable=False),
            sa.Column('label', sa.String(length=100), nullable=False),
            sa.Column('device_name', sa.String(length=100), nullable=False),
            sa.Column('platform', sa.String(length=10), nullable=True, server_default='android'),
            sa.Column('platform_version', sa.String(length=20), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=True, server_default='true'),
            sa.Column('provider_override', sa.String(length=50), nullable=True),
            sa.Column('server_url', sa.String(length=255), nullable=True),
            sa.Column('auth_user', sa.String(length=100), nullable=True),
            sa.Column('auth_token', sa.String(length=255), nullable=True),
            sa.Column('app_identifier', sa.String(length=255), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(
            op.f('ix_product_mobile_devices_id'),
            'product_mobile_devices', ['id'], unique=False
        )

    # Ensure products table has platform and channel_type columns (safe IF NOT EXISTS)
    op.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS platform VARCHAR(20) DEFAULT 'web'")
    op.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS notification_urls TEXT")
    op.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS channel_type VARCHAR(50) DEFAULT 'webhook'")


def downgrade() -> None:
    op.drop_table('product_mobile_devices')
    op.drop_table('product_mobile_settings')
