"""add_sp_get_dashboard_summary

Revision ID: 44bb15272a1a
Revises: 406923006ced
Create Date: 2026-06-10 15:12:14.221730

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '44bb15272a1a'
down_revision: Union[str, Sequence[str], None] = '406923006ced'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    DROP PROCEDURE IF EXISTS sp_get_dashboard_summary;
    """)
    op.execute("""
    CREATE PROCEDURE sp_get_dashboard_summary(
        IN p_user_id INT,
        IN p_is_admin BOOLEAN,
        IN p_is_team_lead BOOLEAN
    )
    BEGIN
        -- Variables for counts
        DECLARE v_total_projects INT DEFAULT 0;
        DECLARE v_active_projects INT DEFAULT 0;
        DECLARE v_total_tasks INT DEFAULT 0;
        DECLARE v_completed_tasks INT DEFAULT 0;
        DECLARE v_total_issues INT DEFAULT 0;
        DECLARE v_open_issues INT DEFAULT 0;
        DECLARE v_total_hours DECIMAL(10,2) DEFAULT 0.0;
        
        -- Simplified logic: If admin, global counts. If not, we should ideally filter by project members.
        -- For maximum performance in the SP, we will return global counts for admin, 
        -- and for regular users we use a subquery to find accessible projects.
        
        IF p_is_admin THEN
            SELECT COUNT(*), SUM(IF(status_id NOT IN (SELECT id FROM master_lookup WHERE label IN ('Completed', 'Closed')), 1, 0))
            INTO v_total_projects, v_active_projects FROM projects;
            
            SELECT COUNT(*), SUM(IF(status_id IN (SELECT id FROM master_lookup WHERE label='Completed'), 1, 0))
            INTO v_total_tasks, v_completed_tasks FROM tasks;
            
            SELECT COUNT(*), SUM(IF(status_id NOT IN (SELECT id FROM master_lookup WHERE label IN ('Completed', 'Closed', 'Resolved')), 1, 0))
            INTO v_total_issues, v_open_issues FROM issues;
            
            SELECT IFNULL(SUM(daily_log_hours), 0.0) INTO v_total_hours FROM time_logs;
        ELSE
            -- Regular User / Team Lead
            -- Fetch accessible projects
            CREATE TEMPORARY TABLE IF NOT EXISTS tmp_user_projects (project_id INT PRIMARY KEY);
            TRUNCATE TABLE tmp_user_projects;
            
            INSERT IGNORE INTO tmp_user_projects (project_id)
            SELECT project_id FROM project_members WHERE user_id = p_user_id
            UNION
            SELECT id FROM projects WHERE owner_id = p_user_id OR project_manager_id = p_user_id;
            
            SELECT COUNT(*), SUM(IF(status_id NOT IN (SELECT id FROM master_lookup WHERE label IN ('Completed', 'Closed')), 1, 0))
            INTO v_total_projects, v_active_projects FROM projects WHERE id IN (SELECT project_id FROM tmp_user_projects);
            
            SELECT COUNT(*), SUM(IF(status_id IN (SELECT id FROM master_lookup WHERE label='Completed'), 1, 0))
            INTO v_total_tasks, v_completed_tasks FROM tasks WHERE project_id IN (SELECT project_id FROM tmp_user_projects) OR assignee_id = p_user_id;
            
            SELECT COUNT(*), SUM(IF(status_id NOT IN (SELECT id FROM master_lookup WHERE label IN ('Completed', 'Closed', 'Resolved')), 1, 0))
            INTO v_total_issues, v_open_issues FROM issues WHERE project_id IN (SELECT project_id FROM tmp_user_projects) OR assignee_id = p_user_id;
            
            SELECT IFNULL(SUM(daily_log_hours), 0.0) INTO v_total_hours FROM time_logs WHERE project_id IN (SELECT project_id FROM tmp_user_projects) OR user_id = p_user_id;
        END IF;

        -- Return the final row
        SELECT 
            v_total_projects AS total_projects,
            v_active_projects AS active_projects,
            v_total_tasks AS total_tasks,
            v_completed_tasks AS completed_tasks,
            v_total_issues AS total_issues,
            v_open_issues AS open_issues,
            v_total_hours AS total_hours_logged;
            
        DROP TEMPORARY TABLE IF EXISTS tmp_user_projects;
    END;
    """)

def downgrade() -> None:
    op.execute("DROP PROCEDURE IF EXISTS sp_get_dashboard_summary;")

