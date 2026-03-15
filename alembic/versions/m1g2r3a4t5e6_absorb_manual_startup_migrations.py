"""absorb manual startup migrations

Absorbs the manual SQL migrations from main.py startup into Alembic,
ensuring columns exist via IF NOT EXISTS (safe to re-run).

Revision ID: m1g2r3a4t5e6
Revises: f3f4e5d6c7b8
Create Date: 2026-03-14
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'm1g2r3a4t5e6'
down_revision = 'f3f4e5d6c7b8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # flow_data columns (previously added manually in main.py startup)
    op.execute("ALTER TABLE flow_data ADD COLUMN IF NOT EXISTS name VARCHAR(255) DEFAULT 'Fluxo Principal'")
    op.execute("ALTER TABLE flow_data ADD COLUMN IF NOT EXISTS flow_type VARCHAR(50) DEFAULT 'api'")
    op.execute("ALTER TABLE flow_data ADD COLUMN IF NOT EXISTS company_id INTEGER")
    op.execute("ALTER TABLE flow_data ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP")
    op.execute("ALTER TABLE flow_data ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP")

    # schedules columns
    op.execute("ALTER TABLE schedules ADD COLUMN IF NOT EXISTS max_concurrency INTEGER")
    op.execute("ALTER TABLE schedules ADD COLUMN IF NOT EXISTS flow_type VARCHAR(20) DEFAULT 'api'")
    op.execute("ALTER TABLE schedules ADD COLUMN IF NOT EXISTS capture_video BOOLEAN DEFAULT FALSE")
    op.execute("ALTER TABLE schedules ADD COLUMN IF NOT EXISTS capture_screenshot BOOLEAN DEFAULT FALSE")

    # Backfill company_id for existing flows
    op.execute("""
        UPDATE flow_data 
        SET company_id = (
            SELECT p.company_id 
            FROM products p 
            JOIN features f ON f.product_id = p.id 
            WHERE f.id = flow_data.project_id
        )
        WHERE company_id IS NULL 
        AND EXISTS (
            SELECT 1 FROM features f 
            JOIN products p ON f.product_id = p.id 
            WHERE f.id = flow_data.project_id
        )
    """)


def downgrade() -> None:
    # These columns were already in prod via manual migration, 
    # so downgrade is a no-op to avoid data loss.
    pass
