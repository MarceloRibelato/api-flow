"""consolidate inline startup migrations

Revision ID: m2g3r4a5t6e7
Revises: p6a7r8t9m0a1
Create Date: 2026-11-21 18:25:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'm2g3r4a5t6e7'
down_revision = 'p6a7r8t9m0a1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # service_mocks
    op.execute("ALTER TABLE service_mocks ADD COLUMN IF NOT EXISTS force_real_api BOOLEAN DEFAULT FALSE NOT NULL;")
    op.execute("ALTER TABLE service_mocks ADD COLUMN IF NOT EXISTS real_first_fallback_mock BOOLEAN DEFAULT FALSE NOT NULL;")
    op.execute("ALTER TABLE service_mocks ADD COLUMN IF NOT EXISTS team_id INTEGER REFERENCES teams(id) ON DELETE CASCADE;")
    op.execute("ALTER TABLE service_mocks ALTER COLUMN product_id DROP NOT NULL;")
    op.execute("ALTER TABLE service_mocks ADD COLUMN IF NOT EXISTS target_url VARCHAR(500);")
    op.execute("ALTER TABLE service_mocks ADD COLUMN IF NOT EXISTS enable_proxy BOOLEAN DEFAULT FALSE;")

    # mock_logs
    op.execute("ALTER TABLE mock_logs ADD COLUMN IF NOT EXISTS is_proxied BOOLEAN DEFAULT FALSE;")

    # products
    op.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS team_id INTEGER REFERENCES teams(id) ON DELETE SET NULL;")

    # flow_versions
    op.execute("ALTER TABLE flow_versions ADD COLUMN IF NOT EXISTS is_definitive BOOLEAN DEFAULT TRUE NOT NULL;")

    # flow_card_data
    op.execute("ALTER TABLE flow_card_data ADD COLUMN IF NOT EXISTS db_queries JSON DEFAULT '[]'::json;")
    op.execute("ALTER TABLE flow_card_data ADD COLUMN IF NOT EXISTS message_queues JSON DEFAULT '[]'::json;")

    # flow_edges
    op.execute("ALTER TABLE flow_edges ADD COLUMN IF NOT EXISTS label VARCHAR(255);")

    # schedules
    op.execute("ALTER TABLE schedules ADD COLUMN IF NOT EXISTS is_manual BOOLEAN DEFAULT FALSE;")

    # companies
    op.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS license_key TEXT;")
    op.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS default_notification_urls VARCHAR(500);")
    op.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS retention_days INTEGER DEFAULT 360;")
    op.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS history_retention_days INTEGER;")
    op.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS history_archive_retention_days INTEGER;")

    # users
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS company_id INTEGER;")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(50) DEFAULT 'admin';")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER DEFAULT 1;")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'active';")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS accepted_terms BOOLEAN DEFAULT FALSE;")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS terms_accepted_at TIMESTAMP;")

    # Default partitions for partitioned execution history tables
    for tbl in (
        "api_execution_history",
        "web_execution_history",
        "mobile_execution_history",
        "performance_test_results",
    ):
        op.execute(f"CREATE TABLE IF NOT EXISTS {tbl}_default PARTITION OF {tbl} DEFAULT;")


def downgrade() -> None:
    # No-op to preserve production data on columns consolidated from startup
    pass
