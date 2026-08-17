"""remove duplicate timelogs

Revision ID: 05dd18cdfad3
Revises: 63f24ae69f12
Create Date: 2026-07-29 19:31:27.369722

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '05dd18cdfad3'
down_revision: Union[str, Sequence[str], None] = '63f24ae69f12'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Remove duplicate timelogs
    op.execute(
        """
        DELETE FROM timelogs 
        WHERE id NOT IN (
            SELECT min_id FROM (
                SELECT MIN(id) AS min_id 
                FROM timelogs 
                GROUP BY 
                    user_id, date, daily_log_hours, 
                    COALESCE(project_id, 0), COALESCE(task_id, 0), COALESCE(issue_id, 0), 
                    COALESCE(notes, ''), COALESCE(public_id, '')
            ) AS temp
        )
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    pass
