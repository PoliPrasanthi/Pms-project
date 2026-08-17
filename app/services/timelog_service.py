from __future__ import annotations

from typing import List, Optional

import asyncio
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.timelog import TimeLog
from app.models.task import Task
from app.models.user import User
from app.schemas.timelog import TimeLogCreate, TimeLogUpdate
from app.utils.ids import generate_public_id, get_next_sequence_id
from app.models.project import Project
from app.utils.audit_utils import capture_audit_details, write_audit
from sqlalchemy import func, case

def _timelog_query():
    return (
        select(TimeLog)
        .options(
            selectinload(TimeLog.user),
            selectinload(TimeLog.created_by),
            selectinload(TimeLog.project),
            selectinload(TimeLog.task).selectinload(Task.project),
            selectinload(TimeLog.issue),
        )
    )


async def get_timelog(db: AsyncSession, timelog_id: int) -> Optional[TimeLog]:
    result = await db.execute(_timelog_query().where(TimeLog.id == timelog_id))
    return result.scalar_one_or_none()


async def get_timelogs(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 20,
    project_id: Optional[int] = None,
    task_id: Optional[int] = None,
    issue_id: Optional[int] = None,
    user_ids: Optional[List[int]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user=None,
    view_level: Optional[str] = None,
) -> List[TimeLog]:
    stmt = select(TimeLog)

    if project_id:
        # Use explicit join to capture timelogs that have task_id set but project_id=NULL
        # (common when timelogs were imported from Zoho with only a task reference)
        stmt = (
            stmt
            .outerjoin(Task, TimeLog.task_id == Task.id)
            .where(
                or_(
                    TimeLog.project_id == project_id,
                    Task.project_id == project_id,
                )
            )
        )
    if task_id:
        stmt = stmt.where(TimeLog.task_id == task_id)
    if issue_id:
        stmt = stmt.where(TimeLog.issue_id == issue_id)
    if user_ids:
        stmt = stmt.where(TimeLog.user_id.in_(user_ids))
    if start_date:
        stmt = stmt.where(TimeLog.date >= start_date)
    if end_date:
        stmt = stmt.where(TimeLog.date <= end_date)
    if current_user is not None and view_level != 'All':
        from app.core.security import normalize_view_level
        norm_level = normalize_view_level(view_level)
        if norm_level == 'O' or norm_level is None:
            stmt = stmt.where(TimeLog.user_id == current_user.id)
        elif norm_level == 'A':
            from app.models.project import Project, ProjectMember
            proj_stmt = select(Project.id).where(
                or_(
                    Project.owner_id == current_user.id,
                    Project.project_manager_id == current_user.id,
                    Project.delivery_head_id == current_user.id,
                    Project.team_members.any(ProjectMember.user_id == current_user.id)
                )
            )
            allowed_project_ids = (await db.execute(proj_stmt)).scalars().all()
            if allowed_project_ids:
                stmt = stmt.where(
                    or_(
                        TimeLog.user_id == current_user.id,
                        TimeLog.project_id.in_(allowed_project_ids)
                    )
                )
            else:
                stmt = stmt.where(TimeLog.user_id == current_user.id)


    count_stmt = stmt.with_only_columns(func.count(TimeLog.id.distinct())).order_by(None)
    
    # Calculate sum of hours without subquery
    hours_stmt = stmt.with_only_columns(
        func.sum(TimeLog.daily_log_hours).label("total_hours"),
        func.sum(
            case((TimeLog.billing_type == 'Billable', TimeLog.daily_log_hours), else_=0)
        ).label("billable_hours"),
        func.sum(
            case((TimeLog.billing_type == 'Non-Billable', TimeLog.daily_log_hours), else_=0)
        ).label("non_billable_hours")
    ).order_by(None)

    count_result = await db.execute(count_stmt)
    hours_result = await db.execute(hours_stmt)
    
    stmt = stmt.options(
        selectinload(TimeLog.user),
        selectinload(TimeLog.created_by),
        selectinload(TimeLog.project),
        selectinload(TimeLog.task).selectinload(Task.project),
        selectinload(TimeLog.issue),
        selectinload(TimeLog.approval_status_master)
    )
    
    data_result = await db.execute(stmt.order_by(TimeLog.date.desc(), TimeLog.created_at.desc()).offset(skip).limit(limit))

    total = count_result.scalar() or 0
    hours_row = hours_result.first()
    
    status_counts = {
        "billable": float(hours_row.billable_hours or 0) if hours_row else 0,
        "nonBillable": float(hours_row.non_billable_hours or 0) if hours_row else 0,
    }
    
    items = data_result.scalars().unique().all()

    return {"total": total, "status_counts": status_counts, "items": items}


async def create_timelog(
    db: AsyncSession,
    timelog: TimeLogCreate,
    actor_id: Optional[str] = None,
    created_by_id: Optional[int] = None,
) -> TimeLog:
    if timelog.task_id:
        from app.models.task import Task
        from sqlalchemy.orm import selectinload
        task = (await db.execute(select(Task).options(selectinload(Task.status_master)).where(Task.id == timelog.task_id))).scalar_one_or_none()
        if task:
            s_name = (task.status_master.label if task.status_master else getattr(task, 'status_name', '')).lower()
            if s_name in ['completed', 'closed', 'done', 'cancelled']:
                from fastapi import HTTPException
                raise HTTPException(status_code=400, detail="Cannot log time on a completed or closed task.")
    project = None
    if timelog.project_id:
        project = (await db.execute(select(Project).where(Project.id == timelog.project_id))).scalar_one_or_none()

    project_name = project.project_name if project else ""
    public_id = await get_next_sequence_id(db, TimeLog, project_name, timelog.project_id, "TL", True) if timelog.project_id else generate_public_id("TL-")

    db_timelog = TimeLog(
        public_id       = public_id,
        user_id         = timelog.user_id,
        created_by_id   = created_by_id,
        project_id      = timelog.project_id,
        task_id         = timelog.task_id,
        issue_id        = timelog.issue_id,
        date            = timelog.date,
        daily_log_hours = timelog.daily_log_hours,
        time_period     = timelog.time_period,
        log_title       = timelog.log_title,
        notes           = timelog.notes,
        billing_type    = timelog.billing_type or "Billable",
        approval_status_id = timelog.approval_status_id,
        general_log     = timelog.general_log or False,
    )
    db.add(db_timelog)
    await db.flush()

    await write_audit(
        db, actor_id, "CREATE", "timelogs",
        timelog.project_id or db_timelog.id, db_timelog.id,
        [{
            "field_name": "daily_log_hours",
            "old_value": None,
            "new_value": f"{timelog.daily_log_hours}h on {timelog.date} — {timelog.log_title or timelog.notes or ''}",
        }],
    )
    await db.commit()
    return await get_timelog(db, db_timelog.id)


async def update_timelog(
    db: AsyncSession,
    timelog_id: int,
    timelog_update: TimeLogUpdate,
    actor_id: Optional[str] = None,
) -> Optional[TimeLog]:
    result = await db.execute(select(TimeLog).where(TimeLog.id == timelog_id))
    db_timelog = result.scalar_one_or_none()
    if not db_timelog:
        return None

    new_task_id = timelog_update.task_id if timelog_update.task_id is not None else db_timelog.task_id
    if new_task_id:
        from app.models.task import Task
        from sqlalchemy.orm import selectinload
        task = (await db.execute(select(Task).options(selectinload(Task.status_master)).where(Task.id == new_task_id))).scalar_one_or_none()
        if task:
            s_name = (task.status_master.label if task.status_master else getattr(task, 'status_name', '')).lower()
            if s_name in ['completed', 'closed', 'done', 'cancelled']:
                from fastapi import HTTPException
                raise HTTPException(status_code=400, detail="Cannot log time on a completed or closed task.")

    update_data = timelog_update.model_dump(exclude_unset=True)

    if "approval_status_id" in update_data and update_data["approval_status_id"] != db_timelog.approval_status_id:
        update_data["previous_approval_status_id"] = db_timelog.approval_status_id
        update_data["is_processed"] = False

    changes = capture_audit_details(db_timelog, update_data)
    for key, value in update_data.items():
        setattr(db_timelog, key, value)

    await write_audit(
        db, actor_id, "UPDATE", "timelogs",
        db_timelog.project_id or timelog_id, timelog_id, changes,
    )
    await db.commit()
    return await get_timelog(db, timelog_id)


async def delete_timelog(
    db: AsyncSession,
    timelog_id: int,
    actor_id: Optional[str] = None,
) -> bool:
    result = await db.execute(select(TimeLog).where(TimeLog.id == timelog_id))
    db_timelog = result.scalar_one_or_none()
    if not db_timelog:
        return False
    await write_audit(
        db, actor_id, "DELETE", "timelogs",
        db_timelog.project_id or timelog_id, timelog_id,
        [{"field_name": "daily_log_hours", "old_value": str(db_timelog.daily_log_hours), "new_value": None}],
    )
    await db.delete(db_timelog)
    await db.commit()
    return True


async def create_timelogs_bulk(
    db: AsyncSession,
    timelogs: List[TimeLogCreate],
    actor_id: Optional[str] = None,
    created_by_id: Optional[int] = None,
) -> List[TimeLog]:
    project_ids = {log.project_id for log in timelogs if log.project_id}
    projects = (await db.execute(select(Project).where(Project.id.in_(project_ids)))).scalars().all() if project_ids else []
    project_map = {p.id: p.project_name for p in projects}

    db_logs = []
    for log in timelogs:
        if (log.daily_log_hours or 0) <= 0:
            continue

        project_name = project_map.get(log.project_id, "")
        public_id = await get_next_sequence_id(db, TimeLog, project_name, log.project_id, "TL", True) if log.project_id else generate_public_id("TL-")

        db_log = TimeLog(
            public_id       = public_id,
            user_id         = log.user_id,
            created_by_id   = created_by_id,
            project_id      = log.project_id,
            task_id         = log.task_id,
            issue_id        = log.issue_id,
            date            = log.date,
            daily_log_hours = log.daily_log_hours,
            time_period     = log.time_period,
            log_title       = log.log_title,
            notes           = log.notes,
            billing_type    = log.billing_type or "Billable",
            approval_status_id = log.approval_status_id,
            general_log     = log.general_log or False,
        )
        db_logs.append(db_log)

    db.add_all(db_logs)
    await db.flush()

    if actor_id and db_logs:
        await write_audit(
            db, actor_id, "CREATE", "timelogs",
            db_logs[0].project_id or db_logs[0].id, db_logs[0].id,
            [{"field_name": "bulk_create", "old_value": None,
              "new_value": f"Bulk created {len(db_logs)} time logs"}],
        )

    await db.commit()

    log_ids = [lg.id for lg in db_logs]
    return (await db.execute(_timelog_query().where(TimeLog.id.in_(log_ids)))).scalars().unique().all()
