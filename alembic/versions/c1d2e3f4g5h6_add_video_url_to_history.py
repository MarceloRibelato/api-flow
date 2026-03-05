"""Add video_url to history tables

Revision ID: c1d2e3f4g5h6
Revises: b1c2d3e4f5a6
Create Date: 2026-02-25 11:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

# revision identifiers, used by Alembic.
revision: str = 'c1d2e3f4g5h6'
down_revision: Union[str, Sequence[str], None] = 'b1c2d3e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = Inspector.from_engine(conn)
    
    # --- Main table ---
    columns = [col['name'] for col in inspector.get_columns('api_test_execution_history')]
    if 'video_url' not in columns:
        op.add_column(
            'api_test_execution_history',
            sa.Column('video_url', sa.String(length=500), nullable=True)
        )

    # --- Archive table ---
    archive_tables = inspector.get_table_names()
    if 'api_test_execution_history_archive' in archive_tables:
        archive_columns = [col['name'] for col in inspector.get_columns('api_test_execution_history_archive')]
        if 'video_url' not in archive_columns:
            op.add_column(
                'api_test_execution_history_archive',
                sa.Column('video_url', sa.String(length=500), nullable=True)
            )


def downgrade() -> None:
    # --- Main table ---
    op.drop_column('api_test_execution_history', 'video_url')

    # --- Archive table ---
    op.drop_column('api_test_execution_history_archive', 'video_url')
