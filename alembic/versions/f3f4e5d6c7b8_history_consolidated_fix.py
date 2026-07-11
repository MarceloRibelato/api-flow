"""History consolidated fix

Revision ID: f3f4e5d6c7b8
Revises: e2f3d4c5b6a7
Create Date: 2026-02-18 17:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

# revision identifiers, used by Alembic.
revision: str = 'f3f4e5d6c7b8'
down_revision: Union[str, Sequence[str], None] = 'e2f3d4c5b6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = Inspector.from_engine(conn)
    columns = [col['name'] for col in inspector.get_columns('api_test_execution_history')]

    # Add missing columns with safety checks
    if 'api_name' not in columns:
        op.add_column('api_test_execution_history', sa.Column('api_name', sa.String(length=255), nullable=True))
    
    if 'feature_name' not in columns:
        op.add_column('api_test_execution_history', sa.Column('feature_name', sa.String(length=255), nullable=True))

    if 'node_name' not in columns:
        op.add_column('api_test_execution_history', sa.Column('node_name', sa.String(length=255), nullable=True))

    if 'schedule_id' not in columns:
        op.add_column('api_test_execution_history', sa.Column('schedule_id', sa.Integer(), nullable=True))
        op.create_index(op.f('ix_api_test_execution_history_schedule_id'), 'api_test_execution_history', ['schedule_id'], unique=False)

    if 'environment_id' not in columns:
        op.add_column('api_test_execution_history', sa.Column('environment_id', sa.Integer(), nullable=True))
        op.create_index(op.f('ix_api_test_execution_history_environment_id'), 'api_test_execution_history', ['environment_id'], unique=False)

    if 'environment_name' not in columns:
        op.add_column('api_test_execution_history', sa.Column('environment_name', sa.String(length=100), nullable=True))

    if 'assertions' not in columns:
        op.add_column('api_test_execution_history', sa.Column('assertions', sa.JSON(), nullable=True))

    if 'user_id' not in columns:
        op.add_column('api_test_execution_history', sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True))
        op.create_index(op.f('ix_api_test_execution_history_user_id'), 'api_test_execution_history', ['user_id'], unique=False)


def downgrade() -> None:
    # Downgrade is optional for fix migrations but let's provide basic rollback
    op.drop_column('api_test_execution_history', 'assertions')
    op.drop_column('api_test_execution_history', 'environment_name')
    op.drop_column('api_test_execution_history', 'environment_id')
    op.drop_column('api_test_execution_history', 'schedule_id')
    op.drop_column('api_test_execution_history', 'node_name')
    op.drop_column('api_test_execution_history', 'feature_name')
    op.drop_column('api_test_execution_history', 'api_name')
    op.drop_column('api_test_execution_history', 'user_id')
