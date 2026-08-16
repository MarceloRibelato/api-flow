"""add trigger_origin to execution history

Revision ID: z9z9z9z9z9za
Revises: z9z9z9z9z9z9
Create Date: 2026-08-10 16:30:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'z9z9z9z9z9za'
down_revision = 'z9z9z9z9z9z9'
branch_labels = None
depends_on = None

def upgrade():
    # add trigger_origin to api_test_execution_history
    op.add_column('api_test_execution_history', sa.Column('trigger_origin', sa.String(length=50), server_default='manual', nullable=True))
    op.create_index(op.f('ix_api_test_execution_history_trigger_origin'), 'api_test_execution_history', ['trigger_origin'], unique=False)

    # add trigger_origin to api_test_execution_history_archive
    op.add_column('api_test_execution_history_archive', sa.Column('trigger_origin', sa.String(length=50), server_default='manual', nullable=True))
    op.create_index(op.f('ix_api_test_execution_history_archive_trigger_origin'), 'api_test_execution_history_archive', ['trigger_origin'], unique=False)

def downgrade():
    op.drop_index(op.f('ix_api_test_execution_history_archive_trigger_origin'), table_name='api_test_execution_history_archive')
    op.drop_column('api_test_execution_history_archive', 'trigger_origin')

    op.drop_index(op.f('ix_api_test_execution_history_trigger_origin'), table_name='api_test_execution_history')
    op.drop_column('api_test_execution_history', 'trigger_origin')
