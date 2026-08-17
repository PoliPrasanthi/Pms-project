"""add_stats_views

Revision ID: 406923006ced
Revises: 60b8b5752aba
Create Date: 2026-06-10 12:37:39.321173

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '406923006ced'
down_revision: Union[str, Sequence[str], None] = '60b8b5752aba'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # v_project_stats view
    op.execute("""
        CREATE OR REPLACE VIEW v_project_stats AS
        SELECT 
            p.id AS project_id,
            COUNT(DISTINCT t.id) AS task_count,
            COUNT(DISTINCT CASE WHEN t.completion_percentage = 100 THEN t.id END) AS completed_task_count,
            COUNT(DISTINCT i.id) AS issue_count,
            COUNT(DISTINCT m.id) AS milestone_count
        FROM projects p
        LEFT JOIN tasks t ON p.id = t.project_id AND t.is_deleted = False
        LEFT JOIN issues i ON p.id = i.project_id AND i.is_deleted = False
        LEFT JOIN milestones m ON p.id = m.project_id AND m.is_deleted = False
        GROUP BY p.id;
    """)

    # v_milestone_stats view
    op.execute("""
        CREATE OR REPLACE VIEW v_milestone_stats AS
        SELECT 
            m.id AS milestone_id,
            COUNT(DISTINCT t.id) AS task_count,
            COUNT(DISTINCT CASE WHEN t.completion_percentage = 100 THEN t.id END) AS completed_task_count,
            COUNT(DISTINCT i.id) AS issue_count
        FROM milestones m
        LEFT JOIN tasks t ON m.id = t.milestone_id AND t.is_deleted = False
        LEFT JOIN issues i ON m.id = i.milestone_id AND i.is_deleted = False
        GROUP BY m.id;
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_project_stats;")
    op.execute("DROP VIEW IF EXISTS v_milestone_stats;")
