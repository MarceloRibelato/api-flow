"""Add batch_id to execution history tables

Revision ID: a1b2c3d4e5f6
Revises: f3f4e5d6c7b8
Create Date: 2026-02-21 13:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'f3f4e5d6c7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = Inspector.from_engine(conn)

    # --- Main table ---
    columns = [col['name'] for col in inspector.get_columns('api_test_execution_history')]
    if 'batch_id' not in columns:
        op.add_column(
            'api_test_execution_history',
            sa.Column('batch_id', sa.String(length=100), nullable=True)
        )
        op.create_index(
            op.f('ix_api_test_execution_history_batch_id'),
            'api_test_execution_history',
            ['batch_id'],
            unique=False
        )

    # --- Archive table ---
    archive_tables = inspector.get_table_names()
    if 'api_test_execution_history_archive' in archive_tables:
        archive_columns = [col['name'] for col in inspector.get_columns('api_test_execution_history_archive')]
        if 'batch_id' not in archive_columns:
            op.add_column(
                'api_test_execution_history_archive',
                sa.Column('batch_id', sa.String(length=100), nullable=True)
            )
            op.create_index(
                op.f('ix_api_test_execution_history_archive_batch_id'),
                'api_test_execution_history_archive',
                ['batch_id'],
                unique=False
            )


def downgrade() -> None:
    # Remove index + column from archive (if exists)
    try:
        op.drop_index(op.f('ix_api_test_execution_history_archive_batch_id'), table_name='api_test_execution_history_archive')
        op.drop_column('api_test_execution_history_archive', 'batch_id')
    except Exception:
        pass

    # Remove index + column from main table
    op.drop_index(op.f('ix_api_test_execution_history_batch_id'), table_name='api_test_execution_history')
    op.drop_column('api_test_execution_history', 'batch_id')
