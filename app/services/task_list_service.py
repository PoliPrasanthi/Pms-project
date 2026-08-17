from __future__ import annotations

from typing import List, Optional

from sqlalchemy import or_, select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.task_list import TaskList
from app.schemas.task_list import TaskListCreate, TaskListUpdate
from app.utils.audit_utils import capture_audit_details, write_audit


def _tl_query():
    return select(TaskList).where(TaskList.is_deleted == False).options(
        selectinload(TaskList.project),
        selectinload(TaskList.milestone),
    )


async def get_task_list(db: AsyncSession, task_list_id: int) -> Optional[TaskList]:
    result = await db.execute(_tl_query().where(TaskList.id == task_list_id))
    return result.scalar_one_or_none()


async def get_task_lists(
    db: AsyncSession,
    project_id: Optional[int] = None,
    skip: int = 0,
    limit: int = 20,
    current_user: Optional[any] = None,
    view_level: Optional[str] = None,
) -> dict:
    from sqlalchemy import select, or_
    from app.models.project import Project, ProjectMember
    from app.core.security import normalize_view_level

    stmt = select(TaskList).where(TaskList.is_deleted == False)
    if project_id:
        stmt = stmt.where(TaskList.project_id == project_id)
        
    user_id = getattr(current_user, "id", None) if current_user else None
    if user_id and view_level:
        norm_level = normalize_view_level(view_level)
        if norm_level == "O":
            from app.models.task import Task
            from app.models.user import User
            stmt = stmt.where(
                or_(
                    TaskList.project.has(
                        or_(
                            Project.owner_id == user_id,
                            Project.project_manager_id == user_id
                        )
                    ),
                    TaskList.created_by_id == user_id,
                    TaskList.tasks.any(
                        or_(
                            Task.assignee_id == user_id,
                            Task.owner_id == user_id,
                            Task.assignees.any(User.id == user_id),
                            Task.owners.any(User.id == user_id)
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
                stmt = stmt.where(TaskList.project_id.in_(allowed_project_ids))
            else:
                stmt = stmt.where(TaskList.id == -1)

    count_stmt = stmt.with_only_columns(func.count(TaskList.id)).order_by(None)
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = stmt.options(
        selectinload(TaskList.project),
        selectinload(TaskList.milestone),
    )

    result = await db.execute(stmt.offset(skip).limit(limit))
    items = result.scalars().unique().all()
    return {"total": total, "items": items}


async def create_task_list(
    db: AsyncSession,
    task_list: TaskListCreate,
    actor_id: Optional[str] = None,
    created_by_id: Optional[int] = None,
) -> TaskList:
    from fastapi import HTTPException

    clean_name = task_list.name.strip()

    stmt = select(TaskList).where(TaskList.name.ilike(clean_name))
    if task_list.project_id is not None:
        stmt = stmt.where(TaskList.project_id == task_list.project_id)
    else:
        stmt = stmt.where(TaskList.project_id.is_(None))

    existing = (await db.execute(stmt)).scalars().first()
    if existing:
        raise HTTPException(status_code=400, detail=f"A task list named '{clean_name}' already exists in this project.")

    db_tl = TaskList(
        name         = clean_name,
        description  = task_list.description,
        project_id   = task_list.project_id,
        milestone_id = task_list.milestone_id,
        created_by_id= created_by_id,
    )
    db.add(db_tl)
    await db.flush()

    await write_audit(
        db, actor_id, "CREATE", "task_lists",
        task_list.project_id or db_tl.id, db_tl.id,
        [{"field_name": "name", "old_value": None, "new_value": clean_name}],
    )
    await db.commit()
    return await get_task_list(db, db_tl.id)


async def get_or_create_general_list(db: AsyncSession, project_id: int) -> TaskList:
    stmt = select(TaskList).where(TaskList.name.ilike("General"), TaskList.project_id == project_id)
    existing = (await db.execute(stmt)).scalars().first()
    if existing:
        return existing

    return await create_task_list(
        db,
        TaskListCreate(name="General", project_id=project_id, description="Default task list for general tasks")
    )


async def update_task_list(
    db: AsyncSession,
    task_list_id: int,
    task_list_update: TaskListUpdate,
    actor_id: Optional[str] = None,
) -> Optional[TaskList]:
    result = await db.execute(select(TaskList).where(TaskList.id == task_list_id))
    db_tl = result.scalar_one_or_none()
    if not db_tl:
        return None

    update_data = task_list_update.model_dump(exclude_unset=True)

    if "name" in update_data:
        new_name = update_data["name"].strip()
        stmt = select(TaskList).where(
            TaskList.name.ilike(new_name),
            TaskList.project_id == db_tl.project_id,
            TaskList.id != task_list_id
        )
        conflict = (await db.execute(stmt)).scalars().first()
        if conflict:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail=f"A task list named '{new_name}' already exists in this project.")
        update_data["name"] = new_name

    changes = capture_audit_details(db_tl, update_data)

    milestone_changed = "milestone_id" in update_data and update_data["milestone_id"] != db_tl.milestone_id
    for k, v in update_data.items():
        setattr(db_tl, k, v)

    if milestone_changed:
        from app.models.task import Task
        from sqlalchemy import update as sa_update
        await db.execute(sa_update(Task).where(Task.task_list_id == task_list_id).values(milestone_id=db_tl.milestone_id))

    await write_audit(db, actor_id, "UPDATE", "task_lists", db_tl.project_id or task_list_id, task_list_id, changes)
    await db.commit()
    return await get_task_list(db, task_list_id)


async def delete_task_list(
    db: AsyncSession,
    task_list_id: int,
    actor_id: Optional[str] = None,
) -> bool:
    result = await db.execute(select(TaskList).where(TaskList.id == task_list_id))
    db_tl = result.scalar_one_or_none()
    if not db_tl:
        return False
    await write_audit(
        db, actor_id, "DELETE", "task_lists",
        db_tl.project_id or task_list_id, task_list_id,
        [{"field_name": "name", "old_value": db_tl.name, "new_value": None}],
    )
    await db.delete(db_tl)
    await db.commit()
    return True


async def search_task_lists(
    db: AsyncSession,
    query: str,
    project_id: Optional[int] = None,
    limit: int = 20,
) -> List[TaskList]:
    if not query:
        return []
    stmt = _tl_query().where(TaskList.name.ilike(f"%{query}%"))
    if project_id:
        stmt = stmt.where(TaskList.project_id == project_id)
    result = await db.execute(stmt.limit(limit))
    return result.scalars().unique().all()
