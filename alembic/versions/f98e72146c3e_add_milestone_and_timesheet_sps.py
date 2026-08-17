"""Add Milestone and Timesheet SPs

Revision ID: f98e72146c3e
Revises: f87e61035b2d
Create Date: 2026-06-10 15:21:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'f98e72146c3e'
down_revision: Union[str, Sequence[str], None] = 'f87e61035b2d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    DROP PROCEDURE IF EXISTS sp_get_milestone_rollups;
    """)
    op.execute("""
    CREATE PROCEDURE sp_get_milestone_rollups(IN p_milestone_id INT)
    BEGIN
        SELECT 
            m.id,
            COUNT(t.id) as total_tasks,
            SUM(IF(ml.label='Completed', 1, 0)) as completed_tasks
        FROM milestones m
        LEFT JOIN task_lists tl ON m.id = tl.milestone_id
        LEFT JOIN tasks t ON tl.id = t.task_list_id
        LEFT JOIN master_lookup ml ON t.status_id = ml.id
        WHERE m.id = p_milestone_id
        GROUP BY m.id;
    END;
    """)

    op.execute("""
    DROP PROCEDURE IF EXISTS sp_get_timesheet_utilization;
    """)
    op.execute("""
    CREATE PROCEDURE sp_get_timesheet_utilization(IN p_user_id INT, IN p_start_date DATE, IN p_end_date DATE)
    BEGIN
        SELECT 
            p.id as project_id,
            SUM(tl.daily_log_hours) as total_hours
        FROM time_logs tl
        JOIN projects p ON tl.project_id = p.id
        WHERE tl.user_id = p_user_id
        AND tl.log_date BETWEEN p_start_date AND p_end_date
        GROUP BY p.id;
    END;
    """)

def downgrade() -> None:
    op.execute("DROP PROCEDURE IF EXISTS sp_get_milestone_rollups;")
    op.execute("DROP PROCEDURE IF EXISTS sp_get_timesheet_utilization;")
