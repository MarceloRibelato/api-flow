"""Add e2e_steps to flow_card_data

Revision ID: b1c2d3e4f5a6
Revises: a1b2c3d4e5f6
Create Date: 2026-02-22 11:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

# revision identifiers, used by Alembic.
revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = Inspector.from_engine(conn)
    columns = [col['name'] for col in inspector.get_columns('flow_card_data')]
    
    if 'e2e_steps' not in columns:
        op.add_column('flow_card_data', sa.Column('e2e_steps', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('flow_card_data', 'e2e_steps')
