from __future__ import annotations

from typing import List, Optional

from sqlalchemy import func, or_, select, text, case
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
import json

from app.models.task import Task
from app.models.user import User
from app.schemas.task import TaskCreate, TaskUpdate, TaskResponse
from app.utils.ids import generate_public_id, get_next_sequence_id
from app.models.project import Project
from app.utils.audit_utils import capture_audit_details, write_audit
from app.models.master import MasterLookup


def _task_query():
    """Base query for single-task lookups — includes all standard relationship loads."""
    return (
        select(Task)
        .where(Task.is_deleted == False)
        .options(
            selectinload(Task.project),
            selectinload(Task.task_list),
            selectinload(Task.milestone),
            selectinload(Task.associated_team),
            selectinload(Task.assignee),
            selectinload(Task.creator),
            selectinload(Task.single_owner),
            selectinload(Task.owners),
            selectinload(Task.assignees),
            selectinload(Task.status_master),
            selectinload(Task.priority_master),
        )
    )


async def get_task(db: AsyncSession, task_id: int) -> Optional[Task]:
    result = await db.execute(_task_query().where(Task.id == task_id))
    return result.scalar_one_or_none()


async def get_active_task_assignees(
    db: AsyncSession,
    current_user=None,
    view_level: str = 'O'
) -> List[User]:
    from app.models.task import task_assignees, task_owners
    
    task_filter = [Task.is_deleted == False]
    
    if current_user and view_level != 'All':
        has_assignee_me = select(1).select_from(task_assignees).where(task_assignees.c.task_id == Task.id, task_assignees.c.user_id == current_user.id).correlate(Task)
        has_owner_me = select(1).select_from(task_owners).where(task_owners.c.task_id == Task.id, task_owners.c.user_id == current_user.id).correlate(Task)
        
        security_conds = [
            Task.assignee_id == current_user.id,
            Task.owner_id == current_user.id,
            Task.created_by_id == current_user.id,
            has_assignee_me.exists(),
            has_owner_me.exists()
        ]

        if view_level == 'A':
            from app.models.project import ProjectMember, Project
            is_member_of_projects = select(Project.id).outerjoin(ProjectMember, ProjectMember.project_id == Project.id).where(
                or_(
                    Project.owner_id == current_user.id,
                    Project.project_manager_id == current_user.id,
                    Project.delivery_head_id == current_user.id,
                    ProjectMember.user_id == current_user.id
                )
            )
            security_conds.append(Task.project_id.in_(is_member_of_projects))
            
        task_filter.append(or_(*security_conds))

    has_task_assignees_table = select(1).select_from(task_assignees).join(Task, task_assignees.c.task_id == Task.id).where(task_assignees.c.user_id == User.id, *task_filter)
    has_task_owners_table = select(1).select_from(task_owners).join(Task, task_owners.c.task_id == Task.id).where(task_owners.c.user_id == User.id, *task_filter)
    
    stmt = select(User).options(selectinload(User.role)).where(
        or_(
            select(1).select_from(Task).where(Task.assignee_id == User.id, *task_filter).exists(),
            select(1).select_from(Task).where(Task.owner_id == User.id, *task_filter).exists(),
            has_task_assignees_table.exists(),
            has_task_owners_table.exists()
        )
    ).order_by(User.first_name, User.last_name)
    
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_tasks(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
    project_id: Optional[int] = None,
    status_ids: Optional[List[int]] = None,
    priority_ids: Optional[List[int]] = None,
    assignee_emails: Optional[List[str]] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    milestone_id: Optional[int] = None,
    search: Optional[str] = None,
    overdue_only: bool = False,
    sort_by: Optional[str] = None,
    current_user=None,
    view_level: str = 'O',
    **kwargs
) -> dict:
    stmt = _task_query()
    from app.models.master import MasterLookup
    count_stmt = select(
        func.count(Task.id).label("total"),
        func.sum(case((MasterLookup.label.in_(["Completed", "Closed", "Done", "Finished"]), 1), else_=0)).label("completed")
    ).outerjoin(MasterLookup, Task.status_id == MasterLookup.id).where(Task.is_deleted == False)

    if project_id is not None:
        stmt = stmt.where(Task.project_id == project_id)
        count_stmt = count_stmt.where(Task.project_id == project_id)
        
    if milestone_id is not None:
        from app.models.task_list import TaskList
        tl_ms_cond = Task.task_list.has(TaskList.milestone_id == milestone_id)
        stmt = stmt.where(or_(Task.milestone_id == milestone_id, tl_ms_cond))
        count_stmt = count_stmt.where(or_(Task.milestone_id == milestone_id, tl_ms_cond))

    if status_ids:
        stmt = stmt.where(Task.status_id.in_(status_ids))
        count_stmt = count_stmt.where(Task.status_id.in_(status_ids))

    if priority_ids:
        stmt = stmt.where(Task.priority_id.in_(priority_ids))
        count_stmt = count_stmt.where(Task.priority_id.in_(priority_ids))

    if search:
        q = f"%{search}%"
        stmt = stmt.where(or_(Task.task_name.ilike(q), Task.public_id.ilike(q)))
        count_stmt = count_stmt.where(or_(Task.task_name.ilike(q), Task.public_id.ilike(q)))

    from datetime import date
    if overdue_only:
        stmt = stmt.where(
            Task.due_date < date.today(),
            ~Task.status_master.has(MasterLookup.label.in_(["Completed", "Closed", "Done", "Finished"]))
        )
        count_stmt = count_stmt.where(
            Task.due_date < date.today(),
            ~Task.status_master.has(MasterLookup.label.in_(["Completed", "Closed", "Done", "Finished"]))
        )

    if assignee_emails:
        from app.models.task import task_assignees, task_owners
        from app.models.user import User
        # Subqueries to match email
        has_assignee = select(1).select_from(task_assignees).join(User, task_assignees.c.user_id == User.id).where(task_assignees.c.task_id == Task.id, User.email.in_(assignee_emails))
        has_owner = select(1).select_from(task_owners).join(User, task_owners.c.user_id == User.id).where(task_owners.c.task_id == Task.id, User.email.in_(assignee_emails))
        
        stmt = stmt.where(
            or_(
                Task.assignee.has(User.email.in_(assignee_emails)),
                has_assignee.exists(),
                has_owner.exists()
            )
        )
        count_stmt = count_stmt.where(
            or_(
                Task.assignee.has(User.email.in_(assignee_emails)),
                has_assignee.exists(),
                has_owner.exists()
            )
        )

    # Apply Security Filter (RBAC) independently of UI filters
    if current_user and view_level != 'All':
        from app.models.task import task_assignees, task_owners
        has_assignee_me = select(1).select_from(task_assignees).where(task_assignees.c.task_id == Task.id, task_assignees.c.user_id == current_user.id).correlate(Task)
        has_owner_me = select(1).select_from(task_owners).where(task_owners.c.task_id == Task.id, task_owners.c.user_id == current_user.id).correlate(Task)
        
        security_conds = [
            Task.assignee_id == current_user.id,
            Task.owner_id == current_user.id,
            Task.created_by_id == current_user.id,
            has_assignee_me.exists(),
            has_owner_me.exists()
        ]

        if view_level == 'A':
            from app.models.project import ProjectMember, Project
            is_member_of_projects = select(Project.id).outerjoin(ProjectMember, ProjectMember.project_id == Project.id).where(
                or_(
                    Project.owner_id == current_user.id,
                    Project.project_manager_id == current_user.id,
                    Project.delivery_head_id == current_user.id,
                    ProjectMember.user_id == current_user.id
                )
            )
            security_conds.append(Task.project_id.in_(is_member_of_projects))
            
        cond = or_(*security_conds)
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    if sort_by:
        sorts = sort_by.split(',')
        for s in sorts:
            if s == 'project':
                from app.models.project import Project
                stmt = stmt.outerjoin(Project, Task.project_id == Project.id).order_by(Project.project_name.asc())
            elif s == 'tasklist':
                from app.models.task_list import TaskList
                stmt = stmt.outerjoin(TaskList, Task.task_list_id == TaskList.id).order_by(TaskList.name.asc())
            elif ':' in s:
                field, order = s.split(':', 1)
                sort_attr = getattr(Task, field, None)
                if sort_attr is not None:
                    if order == 'asc':
                        stmt = stmt.order_by(sort_attr.asc())
                    else:
                        stmt = stmt.order_by(sort_attr.desc())
        stmt = stmt.order_by(Task.id.desc())
    else:
        stmt = stmt.order_by(Task.id.desc())

    # Get total count
    stats_row = (await db.execute(count_stmt)).first()
    total = stats_row.total if stats_row and stats_row.total else 0
    completed = int(stats_row.completed) if stats_row and stats_row.completed else 0
    active = total - completed

    if total == 0:
        return {"total": 0, "status_counts": {"active": 0, "completed": 0}, "items": []}

    # Execute data query
    result = await db.execute(stmt.order_by(Task.id.desc()).offset(skip).limit(limit))
    tasks = result.scalars().unique().all()
    
    if kwargs.get('return_raw'):
        return {"total": total, "status_counts": {"active": active, "completed": completed}, "items": list(tasks)}

    items = []
    for t in tasks:
        item = TaskResponse.model_validate(t).model_dump()
        items.append(item)

    return {"total": total, "status_counts": {"active": active, "completed": completed}, "items": items}


async def search_tasks(
    db: AsyncSession,
    query: str,
    project_id: Optional[int] = None,
    limit: int = 20,
    current_user=None,
    view_level: str = 'O'
) -> List[Task]:
    if not query:
        return []
    q = f"%{query}%"
    stmt = _task_query().where(Task.is_deleted == False, or_(Task.task_name.ilike(q), Task.public_id.ilike(q)))
    if project_id:
        stmt = stmt.where(Task.project_id == project_id)
        
    if current_user and view_level != 'All':
        if view_level == 'O':
            stmt = stmt.where(or_(Task.assignee_id == current_user.id, Task.owner_id == current_user.id, Task.created_by_id == current_user.id))
        elif view_level == 'A':
            from app.models.task import task_assignees, task_owners
            from app.models.project import ProjectMember, Project
            has_assignee = select(1).select_from(task_assignees).where(task_assignees.c.task_id == Task.id, task_assignees.c.user_id == current_user.id)
            has_owner = select(1).select_from(task_owners).where(task_owners.c.task_id == Task.id, task_owners.c.user_id == current_user.id)
            
            is_member_of_projects = select(Project.id).outerjoin(ProjectMember, ProjectMember.project_id == Project.id).where(
                or_(
                    Project.owner_id == current_user.id,
                    Project.project_manager_id == current_user.id,
                    Project.delivery_head_id == current_user.id,
                    ProjectMember.user_id == current_user.id
                )
            )
            stmt = stmt.where(
                or_(
                    Task.assignee_id == current_user.id,
                    Task.owner_id == current_user.id,
                    Task.created_by_id == current_user.id,
                    has_assignee.exists(),
                    has_owner.exists(),
                    Task.project_id.in_(is_member_of_projects)
                )
            )
            
    result = await db.execute(stmt.limit(limit))
    return result.scalars().unique().all()


async def create_task(
    db: AsyncSession,
    task: TaskCreate,
    actor_id: Optional[str] = None,
    created_by_id: Optional[int] = None,
) -> Task:
    project = None
    if task.project_id:
        project = (await db.execute(select(Project).where(Project.id == task.project_id))).scalar_one_or_none()

    project_name = project.project_name if project else ""
    public_id = await get_next_sequence_id(db, Task, project_name, task.project_id, "TSK") if task.project_id else generate_public_id("TSK-")

    if task.task_list_id and not task.milestone_id:
        from app.models.task_list import TaskList
        tl = (await db.execute(select(TaskList).where(TaskList.id == task.task_list_id))).scalar_one_or_none()
        if tl and tl.milestone_id:
            task.milestone_id = tl.milestone_id

    final_task_list_id = task.task_list_id
    if not final_task_list_id and task.project_id:
        from app.services.task_list_service import get_or_create_general_list
        general_list = await get_or_create_general_list(db, task.project_id)
        final_task_list_id = general_list.id

    db_task = Task(
        public_id             = public_id,
        task_name             = task.task_name,
        description           = task.description,
        project_id            = task.project_id,
        task_list_id          = final_task_list_id,
        milestone_id          = task.milestone_id,
        associated_team_id    = task.associated_team_id,
        assignee_id           = task.assignee_id,
        owner_id              = task.owner_id,
        created_by_id         = created_by_id,
        status_id             = task.status_id,
        priority_id           = task.priority_id,
        tags                  = task.tags,
        start_date            = task.start_date,
        due_date              = task.due_date,
        duration              = task.duration,
        completion_percentage = task.completion_percentage or 0,
        estimated_hours       = task.estimated_hours,
        work_hours            = task.work_hours or 0.0,
        billing_type          = task.billing_type or "Billable",
    )

    if db_task.status_id:
        status_rec = (await db.execute(select(MasterLookup).where(MasterLookup.id == db_task.status_id))).scalar_one_or_none()
        if status_rec and status_rec.label == "Completed":
            db_task.completion_percentage = 100
        elif status_rec and status_rec.label in ["Open", "In Progress", "In Review"] and db_task.completion_percentage == 100:
            db_task.completion_percentage = 0

    if task.owner_emails:
        owners = (await db.execute(select(User).where(User.email.in_(task.owner_emails)))).scalars().all()
        db_task.owners.extend(owners)

    if task.assignee_emails:
        assignees = (await db.execute(select(User).where(User.email.in_(task.assignee_emails)))).scalars().all()
        db_task.assignees.extend(assignees)

    db.add(db_task)
    await db.flush()

    await write_audit(
        db, actor_id, "CREATE", "tasks",
        task.project_id or db_task.id, db_task.id,
        [{"field_name": "task_name", "old_value": None, "new_value": task.task_name}],
    )
    await db.commit()
    return await get_task(db, db_task.id)


async def bulk_create_tasks(
    db: AsyncSession,
    tasks: List[TaskCreate],
    actor_id: Optional[str] = None,
    created_by_id: Optional[int] = None,
) -> List[Task]:
    from app.services.task_list_service import get_or_create_general_list
    from app.models.task_list import TaskList

    project_names = {t.project_name for t in tasks if t.project_name and not t.project_id}
    if project_names:
        projects_by_name = (await db.execute(select(Project).where(Project.project_name.in_(project_names)))).scalars().all()
        project_name_map = {p.project_name.lower(): p.id for p in projects_by_name}
        for t in tasks:
            if t.project_name and not t.project_id:
                t.project_id = project_name_map.get(t.project_name.lower())

    project_ids = {t.project_id for t in tasks if t.project_id}
    projects = (await db.execute(select(Project).where(Project.id.in_(project_ids)))).scalars().all() if project_ids else []
    project_map = {p.id: p.project_name for p in projects}

    task_list_names = {t.task_list_name for t in tasks if t.task_list_name and not t.task_list_id}
    if task_list_names:
        tl_query = select(TaskList).where(TaskList.name.in_(task_list_names))
        if project_ids:
            tl_query = tl_query.where(TaskList.project_id.in_(project_ids))
        task_lists_by_name = (await db.execute(tl_query)).scalars().all()
        
        tl_name_map = {}
        for tl in task_lists_by_name:
            tl_name_map[(tl.name.lower(), tl.project_id)] = tl.id
        
        for t in tasks:
            if t.task_list_name and not t.task_list_id and t.project_id:
                t.task_list_id = tl_name_map.get((t.task_list_name.lower(), t.project_id))


    general_list_map = {}
    for pid in project_ids:
        general_list_map[pid] = (await get_or_create_general_list(db, pid)).id

    task_list_ids = {t.task_list_id for t in tasks if t.task_list_id}
    task_lists = (await db.execute(select(TaskList).where(TaskList.id.in_(task_list_ids)))).scalars().all() if task_list_ids else []
    tl_milestone_map = {tl.id: tl.milestone_id for tl in task_lists}

    status_ids_set = {t.status_id for t in tasks if t.status_id}
    status_recs = (await db.execute(select(MasterLookup).where(MasterLookup.id.in_(status_ids_set)))).scalars().all() if status_ids_set else []
    status_label_map = {s.id: s.label for s in status_recs}

    all_emails = set()
    for t in tasks:
        if t.owner_emails: all_emails.update(t.owner_emails)
        if t.assignee_emails: all_emails.update(t.assignee_emails)
    users = (await db.execute(select(User).where(User.email.in_(all_emails)))).scalars().all() if all_emails else []
    user_email_map = {u.email: u for u in users}

    db_tasks = []

    for task in tasks:
        project_name = project_map.get(task.project_id, "")
        public_id = await get_next_sequence_id(db, Task, project_name, task.project_id, "TSK") if task.project_id else generate_public_id("TSK-")

        milestone_id = task.milestone_id
        if task.task_list_id and not milestone_id:
            milestone_id = tl_milestone_map.get(task.task_list_id)

        final_task_list_id = task.task_list_id
        if not final_task_list_id and task.project_id:
            final_task_list_id = general_list_map.get(task.project_id)

        completion_pct = task.completion_percentage or 0
        status_label = status_label_map.get(task.status_id)
        if status_label == "Completed":
            completion_pct = 100
        elif status_label in ["Open", "In Progress", "In Review"] and completion_pct == 100:
            completion_pct = 0

        db_task = Task(
            public_id             = public_id,
            task_name             = task.task_name,
            description           = task.description,
            project_id            = task.project_id,
            task_list_id          = final_task_list_id,
            milestone_id          = milestone_id,
            associated_team_id    = task.associated_team_id,
            assignee_id           = task.assignee_id,
            owner_id              = task.owner_id,
            created_by_id         = created_by_id,
            status_id             = task.status_id,
            priority_id           = task.priority_id,
            tags                  = task.tags,
            start_date            = task.start_date,
            due_date              = task.due_date,
            duration              = task.duration,
            completion_percentage = completion_pct,
            estimated_hours       = task.estimated_hours,
            work_hours            = task.work_hours or 0.0,
            billing_type          = task.billing_type or "Billable",
        )

        if task.owner_emails:
            owners = [user_email_map[e] for e in task.owner_emails if e in user_email_map]
            db_task.owners.extend(owners)

        if task.assignee_emails:
            assignees = [user_email_map[e] for e in task.assignee_emails if e in user_email_map]
            db_task.assignees.extend(assignees)

        db_tasks.append(db_task)

    db.add_all(db_tasks)
    await db.flush()

    for task, db_task in zip(tasks, db_tasks):
        await write_audit(
            db, actor_id, "CREATE", "tasks",
            task.project_id or db_task.id, db_task.id,
            [{"field_name": "task_name", "old_value": None, "new_value": task.task_name}],
        )

    await db.commit()

    task_ids = [t.id for t in db_tasks]
    return (await db.execute(_task_query().where(Task.id.in_(task_ids)))).scalars().unique().all()


async def update_task(
    db: AsyncSession,
    task_id: int,
    task_update: TaskUpdate,
    actor_id: Optional[str] = None,
) -> Optional[Task]:
    result = await db.execute(select(Task).where(Task.id == task_id))
    db_task = result.scalar_one_or_none()
    if not db_task:
        return None

    update_data = task_update.model_dump(
        exclude_unset=True,
        exclude={"owner_emails", "assignee_emails"},
    )

    if "task_list_id" in update_data and update_data["task_list_id"]:
        from app.models.task_list import TaskList
        tl = (await db.execute(select(TaskList).where(TaskList.id == update_data["task_list_id"]))).scalar_one_or_none()
        if tl and tl.milestone_id:
            update_data["milestone_id"] = tl.milestone_id

    if "status_id" in update_data and update_data["status_id"] != db_task.status_id:
        update_data["previous_status_id"] = db_task.status_id
        update_data["is_processed"] = False

    if "priority_id" in update_data and update_data["priority_id"] != db_task.priority_id:
        update_data["is_processed"] = False

    changes = capture_audit_details(db_task, update_data)
    for key, value in update_data.items():
        setattr(db_task, key, value)

    if "status_id" in update_data:
        status_rec = (await db.execute(select(MasterLookup).where(MasterLookup.id == db_task.status_id))).scalar_one_or_none()
        if status_rec and status_rec.label == "Completed":
            db_task.completion_percentage = 100
        elif status_rec and status_rec.label in ["Open", "In Progress", "In Review"] and db_task.completion_percentage == 100:
            db_task.completion_percentage = 0

    if task_update.owner_emails is not None:
        owners = (await db.execute(select(User).where(User.email.in_(task_update.owner_emails)))).scalars().all()
        db_task.owners = list(owners)

    if task_update.assignee_emails is not None:
        assignees = (await db.execute(select(User).where(User.email.in_(task_update.assignee_emails)))).scalars().all()
        db_task.assignees = list(assignees)

    await write_audit(
        db, actor_id, "UPDATE", "tasks",
        db_task.project_id or task_id, task_id, changes,
    )
    await db.commit()
    return await get_task(db, task_id)


async def delete_task(
    db: AsyncSession,
    task_id: int,
    actor_id: Optional[str] = None,
) -> bool:
    result = await db.execute(select(Task).where(Task.id == task_id))
    db_task = result.scalar_one_or_none()
    if not db_task:
        return False
    await write_audit(
        db, actor_id, "DELETE", "tasks",
        db_task.project_id or task_id, task_id, [],
    )
    await db.delete(db_task)
    await db.commit()
    return True
