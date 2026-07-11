"""add_node_id_to_history

Revision ID: e2f3d4c5b6a7
Revises: c88d9b665407
Create Date: 2026-02-17 13:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e2f3d4c5b6a7'
down_revision: Union[str, Sequence[str], None] = 'c88d9b665407'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


from sqlalchemy.engine.reflection import Inspector

def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    inspector = Inspector.from_engine(conn)
    columns = [col['name'] for col in inspector.get_columns('api_test_execution_history')]
    
    if 'node_id' not in columns:
        op.add_column('api_test_execution_history', sa.Column('node_id', sa.String(length=100), nullable=True))
        op.create_index(op.f('ix_api_test_execution_history_node_id'), 'api_test_execution_history', ['node_id'], unique=False)



def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_api_test_execution_history_node_id'), table_name='api_test_execution_history')
    op.drop_column('api_test_execution_history', 'node_id')
