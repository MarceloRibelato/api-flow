"""merge revision heads

Revision ID: f9e8d7c6b5a4
Revises: 208c8ea6809a, p1r2o3d4u5c6
Create Date: 2026-03-30
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f9e8d7c6b5a4'
down_revision = ('208c8ea6809a', 'p1r2o3d4u5c6')
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No changes needed here, this is just a merge point.
    # The actual schema changes are in the individual branches.
    pass


def downgrade() -> None:
    # No changes needed.
    pass
