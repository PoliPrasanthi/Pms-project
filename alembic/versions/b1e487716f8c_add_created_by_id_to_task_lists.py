"""Add created_by_id to task_lists

Revision ID: b1e487716f8c
Revises: 05dd18cdfad3
Create Date: 2026-08-11 12:45:17.780919

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = 'b1e487716f8c'
down_revision: Union[str, Sequence[str], None] = '05dd18cdfad3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('task_lists', sa.Column('created_by_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_task_lists_created_by_id_users', 'task_lists', 'users', ['created_by_id'], ['id'], ondelete='SET NULL')



def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_task_lists_created_by_id_users', 'task_lists', type_='foreignkey')
    op.drop_column('task_lists', 'created_by_id')

