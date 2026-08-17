from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, case, or_, select, text
from app.core.database import get_async_db
from app.core.security import allow_authenticated, is_full_access, is_team_lead_plus
from app.models.project import Project, ProjectMember
from app.models.task import Task, task_assignees, task_owners
from app.models.issue import Issue, issue_assignees, issue_followers
from app.models.timelog import TimeLog
from app.models.milestone import Milestone
from app.models.master import MasterLookup
from app.models.user import User
from app.core.cache import cache

import asyncio
import csv
import io

router = APIRouter(dependencies=[Depends(allow_authenticated)])


@router.get("/summary")
async def get_report_summary(
    db: AsyncSession = Depends(get_async_db),
    current_user = Depends(allow_authenticated)
):
    from app.core.security import get_user_view_level
    role_name = current_user.role.name if current_user.role else ""
    if role_name.lower() not in ["admin", "super admin", "project manager"]:
        proj_level = 'A'
        task_level = 'A'
        issue_level = 'A'
        time_level = 'O'
    else:
        proj_level = get_user_view_level(current_user, 'proj-view')
        task_level = get_user_view_level(current_user, 'task-view')
        issue_level = get_user_view_level(current_user, 'issue-view')
        time_level = get_user_view_level(current_user, 'time-view')

    cache_key = f"dashboard_summary_v2:{current_user.id}"
    cached_data = await cache.get(cache_key)
    if cached_data:
        return cached_data

    proj_filters = []
    if proj_level == 'O':
        proj_filters.append(or_(
            Project.owner_id == current_user.id, 
            Project.project_manager_id == current_user.id,
            Project.team_members.any(ProjectMember.user_id == current_user.id)
        ))
    elif proj_level != 'All':
        proj_filters.append(or_(
            ProjectMember.user_id == current_user.id,
            Project.project_manager_id == current_user.id,
            Project.owner_id == current_user.id,
            Project.delivery_head_id == current_user.id
        ))

    task_filters = []
    has_t_assignee = select(1).select_from(task_assignees).where(task_assignees.c.task_id == Task.id, task_assignees.c.user_id == current_user.id).correlate(Task)
    has_t_owner = select(1).select_from(task_owners).where(task_owners.c.task_id == Task.id, task_owners.c.user_id == current_user.id).correlate(Task)
    
    if task_level == 'O':
        task_filters.append(or_(
            Task.assignee_id == current_user.id,
            Task.owner_id == current_user.id,
            Task.created_by_id == current_user.id,
            has_t_assignee.exists(),
            has_t_owner.exists()
        ))
    elif task_level != 'All':
        task_filters.append(or_(
            ProjectMember.user_id == current_user.id,
            Project.project_manager_id == current_user.id,
            Project.owner_id == current_user.id,
            Project.delivery_head_id == current_user.id,
            Task.assignee_id == current_user.id,
            Task.owner_id == current_user.id,
            Task.created_by_id == current_user.id,
            has_t_assignee.exists(),
            has_t_owner.exists()
        ))

    issue_filters = []
    has_i_assignee = select(1).select_from(issue_assignees).where(issue_assignees.c.issue_id == Issue.id, issue_assignees.c.user_id == current_user.id).correlate(Issue)
    has_i_follower = select(1).select_from(issue_followers).where(issue_followers.c.issue_id == Issue.id, issue_followers.c.user_id == current_user.id).correlate(Issue)
    
    if issue_level == 'O':
        issue_filters.append(or_(
            Issue.assignee_id == current_user.id,
            Issue.reporter_id == current_user.id,
            has_i_assignee.exists(),
            has_i_follower.exists()
        ))
    elif issue_level != 'All':
        issue_filters.append(or_(
            ProjectMember.user_id == current_user.id,
            Project.project_manager_id == current_user.id,
            Project.owner_id == current_user.id,
            Project.delivery_head_id == current_user.id,
            Issue.assignee_id == current_user.id,
            Issue.reporter_id == current_user.id,
            has_i_assignee.exists(),
            has_i_follower.exists()
        ))

    time_filters = []
    if time_level == 'O':
        time_filters.append(TimeLog.user_id == current_user.id)
    elif time_level != 'All':
        time_filters.append(or_(
            ProjectMember.user_id == current_user.id,
            Project.project_manager_id == current_user.id,
            Project.owner_id == current_user.id,
            Project.delivery_head_id == current_user.id,
            TimeLog.user_id == current_user.id
        ))

    # ── KPI counts (Pure ORM, Stored Procedure Removed) ────────────────────────
    proj_query = (
        select(
            func.count(Project.id.distinct()).label("total"),
            func.sum(case((MasterLookup.label.notin_(["Completed", "Closed"]), 1), else_=0)).label("active")
        ).outerjoin(MasterLookup, Project.status_id == MasterLookup.id)
    )
    if proj_filters:
        proj_query = proj_query.outerjoin(
            ProjectMember, 
            (ProjectMember.project_id == Project.id) & (ProjectMember.user_id == current_user.id)
        ).where(*proj_filters)

    task_query = (
        select(
            func.count(Task.id.distinct()).label("total"),
            func.sum(case((MasterLookup.label.in_(["Completed", "Closed", "Done", "Fixed", "Resolved"]), 1), else_=0)).label("completed")
        ).outerjoin(MasterLookup, Task.status_id == MasterLookup.id)
    )
    if task_filters:
        task_query = (
            task_query
            .outerjoin(Project, Task.project_id == Project.id)
            .outerjoin(ProjectMember, (ProjectMember.project_id == Project.id) & (ProjectMember.user_id == current_user.id))
            .where(*task_filters)
        )

    issue_query = (
        select(
            func.count(Issue.id.distinct()).label("total"),
            func.sum(case((MasterLookup.label.notin_(["Completed", "Closed", "Resolved"]), 1), else_=0)).label("open")
        ).outerjoin(MasterLookup, Issue.status_id == MasterLookup.id)
    )
    if issue_filters:
        issue_query = (
            issue_query
            .outerjoin(Project, Issue.project_id == Project.id)
            .outerjoin(ProjectMember, (ProjectMember.project_id == Project.id) & (ProjectMember.user_id == current_user.id))
            .where(*issue_filters)
        )

    hours_query = select(func.sum(TimeLog.daily_log_hours))
    if time_filters:
        hours_query = (
            hours_query
            .outerjoin(Project, TimeLog.project_id == Project.id)
            .outerjoin(ProjectMember, (ProjectMember.project_id == Project.id) & (ProjectMember.user_id == current_user.id))
            .where(*time_filters)
        )

    # ── SEQUENTIAL execution to prevent AsyncSession corruption ───────────────
    proj_res = await db.execute(proj_query)
    task_res = await db.execute(task_query)
    issue_res = await db.execute(issue_query)
    hours_res = await db.execute(hours_query)

    proj_row  = proj_res.first()
    task_row  = task_res.first()
    issue_row = issue_res.first()

    total_projects     = proj_row.total    if proj_row  else 0
    active_projects    = proj_row.active   if proj_row  else 0
    task_total         = task_row.total    if task_row  else 0
    task_completed     = task_row.completed if task_row else 0
    issue_total        = issue_row.total   if issue_row else 0
    issue_open         = issue_row.open    if issue_row else 0
    total_hours_logged = float(hours_res.scalar() or 0.0)
    total_milestones   = 0

    # ── Chart data — 4 queries in parallel ────────────────────────────────────
    t_status_q = (
        select(MasterLookup.label, func.count(Task.id.distinct()))
        .join(Task, Task.status_id == MasterLookup.id)
    )
    if task_filters:
        t_status_q = (
            t_status_q
            .outerjoin(Project, Task.project_id == Project.id)
            .outerjoin(ProjectMember, (ProjectMember.project_id == Project.id) & (ProjectMember.user_id == current_user.id))
            .where(*task_filters)
        )
    t_status_q = t_status_q.group_by(MasterLookup.label)

    p_status_q = (
        select(MasterLookup.label, func.count(Project.id.distinct()))
        .join(Project, Project.status_id == MasterLookup.id)
    )
    if proj_filters:
        p_status_q = p_status_q.outerjoin(
            ProjectMember, 
            (ProjectMember.project_id == Project.id) & (ProjectMember.user_id == current_user.id)
        ).where(*proj_filters)
    p_status_q = p_status_q.group_by(MasterLookup.label)

    i_severity_q = (
        select(MasterLookup.label, func.count(Issue.id.distinct()))
        .join(Issue, Issue.severity_id == MasterLookup.id)
    )
    if issue_filters:
        i_severity_q = (
            i_severity_q
            .outerjoin(Project, Issue.project_id == Project.id)
            .outerjoin(ProjectMember, (ProjectMember.project_id == Project.id) & (ProjectMember.user_id == current_user.id))
            .where(*issue_filters)
        )
    i_severity_q = i_severity_q.group_by(MasterLookup.label)

    prog_q = (
        select(
            Project.project_name,
            func.count(Task.id.distinct()).label('total'),
            func.sum(case((MasterLookup.label.in_(["Completed", "Closed", "Done", "Fixed", "Resolved"]), 1), else_=0)).label('completed')
        )
        .outerjoin(Task, Task.project_id == Project.id)
        .outerjoin(MasterLookup, Task.status_id == MasterLookup.id)
        .where(Project.status_id.notin_(
            select(MasterLookup.id).where(MasterLookup.label.in_(['Completed', 'Closed']))
        ))
    )
    if proj_filters:
        prog_q = prog_q.outerjoin(
            ProjectMember, 
            (ProjectMember.project_id == Project.id) & (ProjectMember.user_id == current_user.id)
        ).where(*proj_filters)
    prog_q = (
        prog_q.group_by(Project.id, Project.project_name)
        .order_by(func.count(Task.id.distinct()).desc())
        .limit(8)
    )

    t_status_res = await db.execute(t_status_q)
    p_status_res = await db.execute(p_status_q)
    i_severity_res = await db.execute(i_severity_q)
    prog_res = await db.execute(prog_q)

    tColors = {'Pending': '#94A3B8', 'In Progress': '#3B82F6', 'Completed': '#10B981', 'Blocked': '#F43F5E'}
    task_status_data = [
        {"name": r[0] or 'Pending', "value": r[1], "color": tColors.get(r[0] or 'Pending', '#8B5CF6')}
        for r in t_status_res.all()
    ]

    pColors = ['#14B8A6', '#6366F1', '#8B5CF6', '#F59E0B', '#EC4899', '#0EA5E9']
    phase_status_data = [
        {"name": r[0] or 'Planning', "value": r[1], "color": pColors[i % len(pColors)]}
        for i, r in enumerate(p_status_res.all())
    ]

    iColors = {'Critical': '#EF4444', 'High': '#F97316', 'Medium': '#F59E0B', 'Low': '#3B82F6'}
    issue_severity_data = [
        {"severity": r[0] or 'Medium', "count": r[1], "fill": iColors.get(r[0] or 'Medium', '#8B5CF6')}
        for r in i_severity_res.all()
    ]

    project_task_progress_data = [
        {
            "name": (r[0] or 'Untitled')[:15] + ('...' if len(r[0] or '') > 15 else ''),
            "total": r[1] or 0,
            "completed": r[2] or 0,
        }
        for r in prog_res.all()
    ]

    result = {
        "total_projects":             int(total_projects   or 0),
        "active_projects":            int(active_projects  or 0),
        "total_tasks":                int(task_total       or 0),
        "completed_tasks":            int(task_completed   or 0),
        "total_issues":               int(issue_total      or 0),
        "open_issues":                int(issue_open       or 0),
        "total_hours_logged":         float(total_hours_logged),
        "total_milestones":           int(total_milestones or 0),
        "task_status_data":           task_status_data,
        "phase_status_data":          phase_status_data,
        "issue_severity_data":        issue_severity_data,
        "project_task_progress_data": project_task_progress_data,
    }

    await cache.set(cache_key, result, expire=300)
    return result

@router.get("/project/{project_id}")
async def get_project_report(project_id: int, db: AsyncSession = Depends(get_async_db)):
    from sqlalchemy.orm import aliased
    project = (await db.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    task_rows = (await db.execute(
        select(MasterLookup.label, func.count(Task.id))
        .join(Task.status_master)
        .where(Task.project_id == project_id)
        .group_by(MasterLookup.label)
    )).all()
    
    tasks_by_status  = [{"status": r[0] or "Unknown", "count": r[1]} for r in task_rows]
    total_tasks      = sum(r["count"] for r in tasks_by_status)
    # Count completed tasks across ALL "done" status variants used in Zoho
    # (not just "Completed" — imported tasks may be "Closed", "Done", "Fixed" etc.)
    COMPLETED_LABELS = {"Completed", "Closed", "Done", "Fixed"}
    completed_tasks  = sum(r["count"] for r in tasks_by_status if r["status"] in COMPLETED_LABELS)

    SeverityLookup = aliased(MasterLookup)
    StatusLookup = aliased(MasterLookup)

    issue_stats = (await db.execute(
        select(
            SeverityLookup.label.label("severity"),
            func.count(Issue.id).label("total"),
            func.sum(case((StatusLookup.label.notin_(["Closed", "Resolved"]), 1), else_=0)).label("open")
        )
        .outerjoin(SeverityLookup, Issue.severity_id == SeverityLookup.id)
        .outerjoin(StatusLookup, Issue.status_id == StatusLookup.id)
        .where(Issue.project_id == project_id)
        .group_by(SeverityLookup.label)
    )).all()

    issues_by_priority = [{"priority": r.severity or "Normal", "count": r.total} for r in issue_stats]
    total_issues       = sum(r.total for r in issue_stats)
    open_issues_count  = sum(r.open for r in issue_stats)

    hours_rows = (await db.execute(
        select(
            User.email,
            User.first_name,
            User.last_name,
            func.sum(TimeLog.daily_log_hours).label("total_hours")
        )
        .join(User, TimeLog.user_id == User.id)
        .where(TimeLog.project_id == project_id)
        .group_by(User.email, User.first_name, User.last_name)
        .order_by(func.sum(TimeLog.daily_log_hours).desc())
        .limit(10)
    )).all()

    hours_by_user = [
        {
            "email": row.email,
            "name":  f"{row.first_name} {row.last_name}".strip() if row.first_name else row.email,
            "hours": float(row.total_hours or 0),
        }
        for row in hours_rows
    ]
    total_hours = sum(r["hours"] for r in hours_by_user)

    total_milestones = (await db.execute(select(func.count(Milestone.id)).where(Milestone.project_id == project_id))).scalar() or 0

    return {
        "project_id":         project_id,
        "project_name":       project.project_name,
        "total_tasks":        total_tasks,
        "completed_tasks":    completed_tasks,
        "total_issues":       total_issues,
        "open_issues":        open_issues_count,
        "total_milestones":   total_milestones,
        "total_hours_logged": total_hours,
        "tasks_by_status":    tasks_by_status,
        "issues_by_priority": issues_by_priority,
        "hours_by_user":      hours_by_user,
    }

@router.get("/export/csv")
async def export_csv_report(report_type: str = "projects", limit: int = 5000, db: AsyncSession = Depends(get_async_db)):
    """
    Export data as CSV. Hard-capped at 5000 rows per request to prevent timeout.
    For larger exports, use the skip/limit pagination parameters.
    """
    _MAX_ROWS = min(limit, 5000)  # Never allow more than 5000 rows per export request
    output = io.StringIO()
    csv_writer = csv.writer(output)
    truncated = False

    if report_type == "projects":
        from sqlalchemy.orm import selectinload
        csv_writer.writerow([
            "Project ID", "Project Name", "Customer Name", "Client Name",
            "Billing Model", "Project Type", "Status", "Priority",
            "Owner", "Project Manager", "Delivery Head",
            "Expected Start", "Expected End", "Actual Start", "Actual End",
            "Estimated Hours", "Actual Hours", "Completion %",
            "Tags", "Description", "Is Archived", "Is Template",
            "Created At", "Updated At"
        ])
        stmt = select(Project).options(
            selectinload(Project.owner),
            selectinload(Project.project_manager),
            selectinload(Project.delivery_head),
            selectinload(Project.status_master),
            selectinload(Project.priority_master)
        ).limit(_MAX_ROWS + 1)
        projects = (await db.execute(stmt)).scalars().all()
        if len(projects) > _MAX_ROWS:
            truncated = True
            projects = projects[:_MAX_ROWS]
        for p in projects:
            owner = getattr(p, "owner", None)
            pm = getattr(p, "project_manager", None)
            dh = getattr(p, "delivery_head", None)
            status_m = getattr(p, "status_master", None)
            priority_m = getattr(p, "priority_master", None)
            csv_writer.writerow([
                p.public_id,
                p.project_name,
                p.customer_name or "",
                p.client_name or "",
                p.billing_model or "",
                p.project_type or "",
                getattr(status_m, "label", "") if status_m else "",
                getattr(priority_m, "label", "") if priority_m else "",
                f"{getattr(owner, 'first_name', '')} {getattr(owner, 'last_name', '')}".strip() if owner else "",
                f"{getattr(pm, 'first_name', '')} {getattr(pm, 'last_name', '')}".strip() if pm else "",
                f"{getattr(dh, 'first_name', '')} {getattr(dh, 'last_name', '')}".strip() if dh else "",
                str(p.expected_start_date or ""),
                str(p.expected_end_date or ""),
                str(p.actual_start_date or ""),
                str(p.actual_end_date or ""),
                p.estimated_hours or 0,
                p.actual_hours or 0,
                p.completion_percentage or 0,
                p.tags or "",
                p.description or "",
                "Yes" if p.is_archived else "No",
                "Yes" if p.is_template else "No",
                str(p.created_at or ""),
                str(p.updated_at or ""),
            ])

    elif report_type == "tasks":
        from sqlalchemy.orm import selectinload
        csv_writer.writerow([
            "Task ID", "Task Name", "Project", "Task List", "Milestone",
            "Status", "Priority", "Assignee", "Owner", "Creator",
            "Start Date", "Due Date", "Completion Date", "Duration",
            "Completion %", "Estimated Hours", "Work Hours", "Logged Hours",
            "Billing Type", "Tags", "Description", "Created At", "Updated At"
        ])
        stmt = select(Task).where(Task.is_deleted == False).options(
            selectinload(Task.project),
            selectinload(Task.task_list),
            selectinload(Task.milestone),
            selectinload(Task.status_master),
            selectinload(Task.priority_master),
            selectinload(Task.assignee),
            selectinload(Task.single_owner),
            selectinload(Task.creator)
        ).limit(_MAX_ROWS + 1)
        tasks = (await db.execute(stmt)).scalars().all()
        if len(tasks) > _MAX_ROWS:
            truncated = True
            tasks = tasks[:_MAX_ROWS]
        for t in tasks:
            proj = getattr(t, "project", None)
            tl = getattr(t, "task_list", None)
            ms = getattr(t, "milestone", None)
            sm = getattr(t, "status_master", None)
            pm = getattr(t, "priority_master", None)
            assignee = getattr(t, "assignee", None)
            owner = getattr(t, "single_owner", None)
            creator = getattr(t, "creator", None)
            
            logged_val = getattr(t, "cached_timelog_total", None) or getattr(t, "timelog_total", 0)
            
            csv_writer.writerow([
                t.public_id,
                t.task_name,
                getattr(proj, "project_name", "") if proj else "",
                getattr(tl, "name", "") if tl else "",
                getattr(ms, "name", "") if ms else "",
                getattr(sm, "label", "") if sm else "",
                getattr(pm, "label", "") if pm else "",
                f"{getattr(assignee, 'first_name', '')} {getattr(assignee, 'last_name', '')}".strip() if assignee else "",
                f"{getattr(owner, 'first_name', '')} {getattr(owner, 'last_name', '')}".strip() if owner else "",
                f"{getattr(creator, 'first_name', '')} {getattr(creator, 'last_name', '')}".strip() if creator else "",
                str(t.start_date or ""),
                str(t.due_date or ""),
                str(t.completion_date or ""),
                t.duration or "",
                t.completion_percentage or 0,
                t.estimated_hours or 0,
                t.work_hours or 0,
                logged_val or 0,
                t.billing_type or "",
                t.tags or "",
                t.description or "",
                str(t.created_at or ""),
                str(t.updated_at or ""),
            ])

    elif report_type == "issues":
        from sqlalchemy.orm import selectinload
        csv_writer.writerow([
            "Issue ID", "Bug Name", "Project", "Milestone",
            "Status", "Priority", "Severity", "Classification", "Module",
            "Assignee", "Reporter", "Reproducible", "Flag",
            "Start Date", "Due Date", "Estimated Hours",
            "Tags", "Description", "Created At", "Updated At"
        ])
        stmt = select(Issue).where(Issue.is_deleted == False).options(
            selectinload(Issue.project),
            selectinload(Issue.milestone),
            selectinload(Issue.status_master),
            selectinload(Issue.priority_master),
            selectinload(Issue.severity_master),
            selectinload(Issue.classification_master),
            selectinload(Issue.assignee),
            selectinload(Issue.reporter)
        ).limit(_MAX_ROWS + 1)
        issues = (await db.execute(stmt)).scalars().all()
        if len(issues) > _MAX_ROWS:
            truncated = True
            issues = issues[:_MAX_ROWS]
        for i in issues:
            proj = getattr(i, "project", None)
            ms = getattr(i, "milestone", None)
            sm = getattr(i, "status_master", None)
            pm = getattr(i, "priority_master", None)
            sev = getattr(i, "severity_master", None)
            cls = getattr(i, "classification_master", None)
            assignee = getattr(i, "assignee", None)
            reporter = getattr(i, "reporter", None)
            csv_writer.writerow([
                i.public_id,
                i.bug_name,
                getattr(proj, "project_name", "") if proj else "",
                getattr(ms, "name", "") if ms else "",
                getattr(sm, "label", "") if sm else "",
                getattr(pm, "label", "") if pm else "",
                getattr(sev, "label", "") if sev else "",
                getattr(cls, "label", "") if cls else "",
                i.module or "",
                f"{getattr(assignee, 'first_name', '')} {getattr(assignee, 'last_name', '')}".strip() if assignee else "",
                f"{getattr(reporter, 'first_name', '')} {getattr(reporter, 'last_name', '')}".strip() if reporter else "",
                "Yes" if i.reproducible_flag else "No",
                i.flag or "",
                str(i.start_date or ""),
                str(i.due_date or ""),
                i.estimated_hours or 0,
                i.tags or "",
                i.description or "",
                str(i.created_at or ""),
                str(i.updated_at or ""),
            ])

    elif report_type == "timelogs":
        from sqlalchemy.orm import selectinload
        csv_writer.writerow([
            "Timelog ID", "User", "Project", "Task", "Issue", "Log Title",
            "Log Date", "Hours", "Time Period", "Is Billable",
            "Approval Status", "Created By", "Notes", "Created At"
        ])
        stmt = select(TimeLog).options(
            selectinload(TimeLog.user),
            selectinload(TimeLog.created_by),
            selectinload(TimeLog.project),
            selectinload(TimeLog.task),
            selectinload(TimeLog.issue),
            selectinload(TimeLog.approval_status_master)
        ).limit(_MAX_ROWS + 1)
        timelogs = (await db.execute(stmt)).scalars().unique().all()
        if len(timelogs) > _MAX_ROWS:
            truncated = True
            timelogs = timelogs[:_MAX_ROWS]
        for tl in timelogs:
            u = getattr(tl, "user", None)
            cb = getattr(tl, "created_by", None)
            proj = getattr(tl, "project", None)
            task = getattr(tl, "task", None)
            issue = getattr(tl, "issue", None)
            approval = getattr(tl, "approval_status_master", None)
            csv_writer.writerow([
                tl.public_id or tl.id,
                f"{getattr(u, 'first_name', '')} {getattr(u, 'last_name', '')}".strip() if u else "",
                getattr(proj, "project_name", "") if proj else "",
                getattr(task, "task_name", "") if task else "",
                getattr(issue, "bug_name", "") if issue else "",
                tl.log_title or "",
                str(tl.date or ""),
                tl.daily_log_hours,
                tl.time_period or "",
                "Yes" if tl.billing_type == "Billable" else "No",
                getattr(approval, "label", "") if approval else "",
                f"{getattr(cb, 'first_name', '')} {getattr(cb, 'last_name', '')}".strip() if cb else "",
                tl.notes or "",
                str(tl.created_at or ""),
            ])

    output.seek(0)
    headers = {"Content-Disposition": f"attachment; filename={report_type}_report.csv"}
    if truncated:
        headers["X-Truncated"] = f"true; max-rows={_MAX_ROWS}"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers=headers,
    )
