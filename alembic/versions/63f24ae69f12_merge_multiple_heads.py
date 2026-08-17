"""merge multiple heads

Revision ID: 63f24ae69f12
Revises: 44bb15272a1a, f98e72146c3e
Create Date: 2026-07-13 17:15:57.181193

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '63f24ae69f12'
down_revision: Union[str, Sequence[str], None] = ('44bb15272a1a', 'f98e72146c3e')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
