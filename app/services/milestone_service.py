from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select, case
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.milestone import Milestone
from app.models.task import Task
from app.models.issue import Issue
from app.schemas.milestone import MilestoneCreate, MilestoneUpdate
from app.utils.ids import generate_public_id
from app.utils.audit_utils import capture_audit_details, write_audit
from sqlalchemy import func


def _milestone_query():
    from sqlalchemy import or_
    from app.models.project import Project
    return (
        select(Milestone)
        .where(or_(Milestone.is_deleted == False, Milestone.is_deleted == None))
        .options(
            selectinload(Milestone.project).selectinload(Project.owner),
            selectinload(Milestone.project).selectinload(Project.project_manager),
            selectinload(Milestone.project).selectinload(Project.delivery_head),
            selectinload(Milestone._owner_rel),
            selectinload(Milestone.stats),
            selectinload(Milestone.status_master),
            selectinload(Milestone.priority_master),
        )
    )

async def _enrich_milestone(db: AsyncSession, milestone: Milestone) -> Milestone:
    await _batch_enrich_milestones(db, [milestone])
    return milestone

async def _batch_enrich_milestones(db: AsyncSession, milestones: List[Milestone]) -> None:
    if not milestones:
        return
    
    from sqlalchemy import select, func, case, or_, update as sa_update
    from app.models.task import Task
    from app.models.task_list import TaskList
    from app.models.issue import Issue
    from app.models.master import MasterLookup
    
    ms_ids = [m.id for m in milestones if getattr(m, "id", None)]
    if not ms_ids:
        return

    # Auto-heal orphaned task lists and tasks whose name matches a milestone inside the same project
    for m in milestones:
        if m.project_id and m.milestone_name:
            clean_ms_name = m.milestone_name.strip().lower()
            try:
                # Find task lists in the project matching the milestone name exactly or closely
                tl_match = (await db.execute(
                    select(TaskList.id).where(
                        TaskList.project_id == m.project_id,
                        TaskList.milestone_id.is_(None),
                        or_(
                            func.lower(TaskList.name) == clean_ms_name,
                            func.lower(TaskList.name).like(f"%{clean_ms_name}%")
                        )
                    )
                )).scalars().all()
                if tl_match:
                    await db.execute(sa_update(TaskList).where(TaskList.id.in_(tl_match)).values(milestone_id=m.id))
                    await db.execute(sa_update(Task).where(Task.task_list_id.in_(tl_match), Task.milestone_id.is_(None)).values(milestone_id=m.id))
                    await db.flush()
            except Exception as e:
                import logging
                logging.error(f"Auto-heal failed for milestone {m.id}: {e}")
                pass
        
    task_stats_stmt = select(
        func.coalesce(Task.milestone_id, TaskList.milestone_id).label("ms_id"),
        func.count(Task.id).label("total_tasks"),
        func.sum(
            case(
                (
                    or_(
                        func.lower(MasterLookup.label).in_(["completed", "closed", "done", "finished", "resolved"]),
                        func.lower(MasterLookup.value).in_(["completed", "closed", "done", "finished", "resolved"]),
                        Task.completion_percentage >= 100
                    ),
                    1
                ),
                else_=0
            )
        ).label("completed_tasks")
    ).outerjoin(TaskList, Task.task_list_id == TaskList.id).outerjoin(MasterLookup, Task.status_id == MasterLookup.id).where(
        or_(Task.milestone_id.in_(ms_ids), TaskList.milestone_id.in_(ms_ids)),
        or_(Task.is_deleted == False, Task.is_deleted == None)
    ).group_by(func.coalesce(Task.milestone_id, TaskList.milestone_id))
    
    issue_stats_stmt = select(
        Issue.milestone_id,
        func.count(Issue.id).label("total_issues")
    ).where(
        Issue.milestone_id.in_(ms_ids),
        or_(Issue.is_deleted == False, Issue.is_deleted == None)
    ).group_by(Issue.milestone_id)
    
    task_rows = (await db.execute(task_stats_stmt)).all()
    issue_rows = (await db.execute(issue_stats_stmt)).all()
    
    task_counts = {row.ms_id: int(row.total_tasks or 0) for row in task_rows if row.ms_id is not None}
    completed_counts = {row.ms_id: int(row.completed_tasks or 0) for row in task_rows if row.ms_id is not None}
    issue_counts = {row.milestone_id: int(row.total_issues or 0) for row in issue_rows if row.milestone_id is not None}
    
    for m in milestones:
        t_count = task_counts.get(m.id, 0)
        c_count = completed_counts.get(m.id, 0)
        i_count = issue_counts.get(m.id, 0)
        
        stat_t_count = m.stats.task_count if getattr(m, "stats", None) and getattr(m.stats, "task_count", None) else 0
        stat_c_count = m.stats.completed_task_count if getattr(m, "stats", None) and getattr(m.stats, "completed_task_count", None) else 0
        stat_i_count = m.stats.issue_count if getattr(m, "stats", None) and getattr(m.stats, "issue_count", None) else 0
        
        final_t_count = max(t_count, stat_t_count)
        final_c_count = max(c_count, stat_c_count)
        final_i_count = max(i_count, stat_i_count)
        
        m._dynamic_task_count = final_t_count
        m._dynamic_completed_task_count = final_c_count
        m._dynamic_issue_count = final_i_count
        
        if final_t_count > 0:
            m.completion_percentage = int(round((final_c_count / final_t_count) * 100))
        elif stat_t_count > 0 and stat_c_count > 0:
            m.completion_percentage = int(round((stat_c_count / stat_t_count) * 100))
        elif m.completion_percentage is None:
            m.completion_percentage = 0

        # Inherit owner from project if not set on milestone
        if not getattr(m, "owner", None) and not m.owner_id and getattr(m, "project", None):
            if getattr(m.project, "owner", None):
                m._dynamic_owner = m.project.owner
                m.owner_id = m.project.owner_id
            elif getattr(m.project, "project_manager", None):
                m._dynamic_owner = m.project.project_manager
                m.owner_id = m.project.project_manager_id
            elif getattr(m.project, "delivery_head", None):
                m._dynamic_owner = m.project.delivery_head
                m.owner_id = m.project.delivery_head_id

        # Populate dynamic status if no status_master set
        if not getattr(m, "status_master", None) and not m.status_id:
            pct = getattr(m, "completion_percentage", 0) or 0
            if pct >= 100:
                m._dynamic_status = {
                    "id": 4,
                    "value": "completed",
                    "label": "Completed",
                    "color": "#10b981"
                }
            elif pct > 0:
                m._dynamic_status = {
                    "id": 2,
                    "value": "in_progress",
                    "label": "In Progress",
                    "color": "#3b82f6"
                }
            else:
                m._dynamic_status = {
                    "id": 1,
                    "value": "in_progress",
                    "label": "In Progress",
                    "color": "#3b82f6"
                }


async def get_milestone(db: AsyncSession, milestone_id: int) -> Optional[Milestone]:
    result = await db.execute(_milestone_query().where(Milestone.id == milestone_id))
    ms = result.scalar_one_or_none()
    if ms: return await _enrich_milestone(db, ms)
    return None

async def get_milestones(
    db: AsyncSession,
    project_id: Optional[int] = None,
    skip: int = 0,
    limit: int = 100,
    current_user: Optional[any] = None,
    view_level: Optional[str] = None,
) -> dict:
    from sqlalchemy import select, or_
    from app.models.project import Project, ProjectMember
    from app.core.security import normalize_view_level

    stmt = select(Milestone).where(or_(Milestone.is_deleted == False, Milestone.is_deleted == None))
    if project_id:
        stmt = stmt.where(Milestone.project_id == project_id)
        
    user_id = getattr(current_user, "id", None) if current_user else None
    if user_id and view_level:
        norm_level = normalize_view_level(view_level)
        if norm_level == "O":
            stmt = stmt.where(
                or_(
                    Milestone.owner_id == user_id,
                    Milestone.project.has(
                        or_(
                            Project.owner_id == user_id,
                            Project.project_manager_id == user_id,
                            Project.team_members.any(ProjectMember.user_id == user_id)
                        )
                    )
                )
            )
        elif norm_level == "A":
            proj_stmt = select(Project.id).where(
                Project.is_deleted == False,
                or_(
                    Project.owner_id == user_id,
                    Project.project_manager_id == user_id,
                    Project.delivery_head_id == user_id,
                    Project.team_members.any(ProjectMember.user_id == user_id)
                )
            )
            allowed_project_ids = (await db.execute(proj_stmt)).scalars().all()
            if allowed_project_ids:
                stmt = stmt.where(
                    or_(
                        Milestone.owner_id == user_id,
                        Milestone.project_id.in_(allowed_project_ids)
                    )
                )
            else:
                stmt = stmt.where(Milestone.owner_id == user_id)

    count_stmt = stmt.with_only_columns(func.count(Milestone.id)).order_by(None)
    total = (await db.execute(count_stmt)).scalar() or 0
    
    stmt = stmt.options(
        selectinload(Milestone.project).selectinload(Project.owner),
        selectinload(Milestone.project).selectinload(Project.project_manager),
        selectinload(Milestone.project).selectinload(Project.delivery_head),
        selectinload(Milestone._owner_rel),
        selectinload(Milestone.stats),
        selectinload(Milestone.status_master),
        selectinload(Milestone.priority_master),
    )
    
    result = await db.execute(stmt.order_by(Milestone.id.desc()).offset(skip).limit(limit))
    milestones = list(result.scalars().unique().all())
    await _batch_enrich_milestones(db, milestones)
    return {"total": total, "items": milestones}

async def create_milestone(
    db: AsyncSession,
    milestone: MilestoneCreate,
    actor_id: Optional[str] = None,
) -> Milestone:
    public_id = generate_public_id("MLS-")
    db_milestone = Milestone(
        public_id      = public_id,
        milestone_name = milestone.milestone_name,
        description    = milestone.description,
        start_date     = milestone.start_date,
        end_date       = milestone.end_date,
        project_id     = milestone.project_id,
        owner_id       = milestone.owner_id,
        status_id      = milestone.status_id,
        priority_id    = milestone.priority_id,
        flags          = milestone.flags,
        tags           = milestone.tags,
    )
    db.add(db_milestone)
    await db.flush()

    await write_audit(
        db, actor_id, "CREATE", "milestones",
        milestone.project_id or db_milestone.id, db_milestone.id,
        [{"field_name": "milestone_name", "old_value": None, "new_value": milestone.milestone_name}],
    )
    await db.commit()
    return await get_milestone(db, db_milestone.id)

async def update_milestone(
    db: AsyncSession,
    milestone_id: int,
    milestone_update: MilestoneUpdate,
    actor_id: Optional[str] = None,
) -> Optional[Milestone]:
    result = await db.execute(select(Milestone).where(Milestone.id == milestone_id))
    db_milestone = result.scalar_one_or_none()
    if not db_milestone:
        return None

    update_data = milestone_update.model_dump(exclude_unset=True)

    if "status_id" in update_data and update_data["status_id"] != db_milestone.status_id:
        update_data["previous_status_id"] = db_milestone.status_id
        update_data["is_processed"] = False

    if "priority_id" in update_data and update_data["priority_id"] != db_milestone.priority_id:
        update_data["is_processed"] = False

    changes = capture_audit_details(db_milestone, update_data)
    for key, value in update_data.items():
        setattr(db_milestone, key, value)

    await write_audit(
        db, actor_id, "UPDATE", "milestones",
        db_milestone.project_id or milestone_id, milestone_id, changes,
    )
    await db.commit()
    return await get_milestone(db, milestone_id)


async def delete_milestone(
    db: AsyncSession,
    milestone_id: int,
    actor_id: Optional[str] = None,
) -> bool:
    result = await db.execute(select(Milestone).where(Milestone.id == milestone_id))
    db_milestone = result.scalar_one_or_none()
    if not db_milestone:
        return False
    await write_audit(
        db, actor_id, "DELETE", "milestones",
        db_milestone.project_id or milestone_id, milestone_id,
        [{"field_name": "milestone_name", "old_value": db_milestone.milestone_name, "new_value": None}],
    )
    await db.delete(db_milestone)
    await db.commit()
    return True
