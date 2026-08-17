from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, exists

from app.core.database import get_async_db
from app.core.cache import endpoint_cache, invalidate_cache
from app.core.security import (
    allow_authenticated,
    allow_task_create,
    allow_task_view,
    allow_task_delete,
    check_task_owner_or_lead,
    is_employee_only,
    is_full_access,
)

from app.schemas.user import UserBase
from app.schemas.task import TaskCreate, TaskUpdate, TaskResponse, TaskListResponse
from app.services import task_service

router = APIRouter(dependencies=[Depends(allow_authenticated)])


@router.post("/", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    task: TaskCreate,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_task_create),
):
    result = await task_service.create_task(
        db=db,
        task=task,
        actor_id=current_user.o365_id or str(current_user.id),
        created_by_id=current_user.id,
    )
    await invalidate_cache("api:tasks_list")
    return result


@router.post("/bulk", response_model=List[TaskResponse])
async def bulk_create_tasks(
    tasks: List[TaskCreate],
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_task_create),
):
    result = await task_service.bulk_create_tasks(
        db=db,
        tasks=tasks,
        actor_id=current_user.o365_id or str(current_user.id),
        created_by_id=current_user.id,
    )
    await invalidate_cache("api:tasks_list")
    return result


@router.get("/search", response_model=List[TaskResponse])
@endpoint_cache(ttl=60, prefix="api:tasks_list")
async def search_tasks(
    q: str = Query(..., min_length=1),
    project_id: Optional[int] = Query(None),
    limit: int = 20,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_authenticated),
):
    from app.core.security import get_user_view_level
    view_level = get_user_view_level(current_user, 'task-view')
    return await task_service.search_tasks(
        db, query=q, project_id=project_id, limit=limit,
        current_user=current_user, view_level=view_level
    )


@router.get("/assignees", response_model=List[UserBase])
@endpoint_cache(ttl=60, prefix="api:tasks_assignees")
async def get_active_task_assignees(
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_authenticated),
):
    from app.core.security import get_user_view_level
    view_level = get_user_view_level(current_user, 'task-view')
    users = await task_service.get_active_task_assignees(
        db, current_user=current_user, view_level=view_level
    )
    return [UserBase.model_validate(u) for u in users]


@router.get("/", response_model=TaskListResponse)
@endpoint_cache(ttl=60, prefix="api:tasks_list")
async def read_tasks(
    skip: int = 0,
    limit: int = 100,
    project_id: Optional[int] = None,
    assignee_email: Optional[List[str]] = Query(None),
    status_id: Optional[List[int]] = Query(None),
    priority_id: Optional[List[int]] = Query(None),
    milestone_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None),
    overdue_only: bool = Query(False),
    sort_by: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_task_view),
):
    from app.core.security import get_user_view_level
    view_level = get_user_view_level(current_user, 'task-view')

    # The RBAC and view_level filtering is entirely handled in task_service.py now.

    return await task_service.get_tasks(
        db,
        skip=skip,
        limit=limit,
        project_id=project_id,
        status_ids=status_id,
        priority_ids=priority_id,
        milestone_id=milestone_id,
        assignee_emails=assignee_email,
        search=search,
        overdue_only=overdue_only,
        sort_by=sort_by,
        current_user=current_user,
        view_level=view_level
    )


@router.get("/export")
async def export_tasks(
    project_id: Optional[int] = None,
    assignee_email: Optional[List[str]] = Query(None),
    status_id: Optional[List[int]] = Query(None),
    priority_id: Optional[List[int]] = Query(None),
    milestone_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None),
    overdue_only: bool = Query(False),
    sort_by: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_task_view),
):
    from app.core.security import get_user_view_level
    import csv
    import io
    from fastapi.responses import StreamingResponse

    view_level = get_user_view_level(current_user, 'task-view')

    if view_level == 'O':
        assignee_email = [current_user.email]
    elif view_level == 'A':
        if project_id:
            from app.models.project import ProjectMember, Project
            is_member = (await db.execute(
                select(Project.id).outerjoin(ProjectMember, ProjectMember.project_id == Project.id).where(
                    Project.id == project_id,
                    or_(
                        Project.owner_id == current_user.id,
                        Project.project_manager_id == current_user.id,
                        Project.delivery_head_id == current_user.id,
                        ProjectMember.user_id == current_user.id
                    )
                )
            )).first() is not None

            if not is_member:
                if assignee_email is None:
                    assignee_email = [current_user.email]

    data = await task_service.get_tasks(
        db,
        skip=0,
        limit=10000,
        project_id=project_id,
        status_ids=status_id,
        priority_ids=priority_id,
        milestone_id=milestone_id,
        assignee_emails=assignee_email,
        search=search,
        overdue_only=overdue_only,
        sort_by=sort_by,
        current_user=current_user,
        view_level=view_level,
        return_raw=True
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Task ID", "Task Name", "Project", "Task List", "Milestone",
        "Status", "Priority", "Assignee", "Co-Assignees", "Owner", "Co-Owners", "Creator",
        "Associated Team", "Start Date", "Due Date", "Completion Date", "Duration",
        "Completion %", "Estimated Hours", "Work Hours", "Logged Hours", "Difference",
        "Billing Type", "Tags", "Description", "Created At", "Updated At"
    ])

    for t in data.get("items", []):
        assignee = getattr(t, "assignee", None)
        owner = getattr(t, "single_owner", None)
        creator = getattr(t, "creator", None)

        assignee_name = f"{getattr(assignee, 'first_name', '')} {getattr(assignee, 'last_name', '')}".strip() if assignee else ""
        owner_name = f"{getattr(owner, 'first_name', '')} {getattr(owner, 'last_name', '')}".strip() if owner else ""
        creator_name = f"{getattr(creator, 'first_name', '')} {getattr(creator, 'last_name', '')}".strip() if creator else ""

        # Co-assignees
        co_assignees = getattr(t, "assignees", []) or []
        co_assignee_names = ", ".join(
            f"{getattr(a, 'first_name', '')} {getattr(a, 'last_name', '')}".strip()
            for a in co_assignees
        )

        # Co-owners
        co_owners = getattr(t, "owners", []) or []
        co_owner_names = ", ".join(
            f"{getattr(o, 'first_name', '')} {getattr(o, 'last_name', '')}".strip()
            for o in co_owners
        )

        # Associated team
        team = getattr(t, "associated_team", None)
        team_name = getattr(team, "name", "") if team else ""

        proj = getattr(t, "project", None)
        task_list = getattr(t, "task_list", None)
        milestone = getattr(t, "milestone", None)
        status_master = getattr(t, "status_master", None)
        priority_master = getattr(t, "priority_master", None)

        logged_val = getattr(t, "cached_timelog_total", None) or getattr(t, "timelog_total", 0)
        try:
            logged = float(logged_val)
        except (ValueError, TypeError):
            logged = 0.0

        est_val = getattr(t, "estimated_hours", 0) or 0
        try:
            est = float(est_val)
        except (ValueError, TypeError):
            est = 0.0

        work_val = getattr(t, "work_hours", 0) or 0
        try:
            work = float(work_val)
        except (ValueError, TypeError):
            work = 0.0

        plan_hours = work or est
        difference = round(plan_hours - logged, 2)

        writer.writerow([
            getattr(t, "public_id", ""),
            getattr(t, "task_name", ""),
            getattr(proj, "project_name", "") if proj else "",
            getattr(task_list, "name", "") if task_list else "",
            getattr(milestone, "name", "") if milestone else "",
            getattr(status_master, "label", "") if status_master else "",
            getattr(priority_master, "label", "") if priority_master else "",
            assignee_name, co_assignee_names, owner_name, co_owner_names, creator_name,
            team_name,
            getattr(t, "start_date", ""),
            getattr(t, "due_date", ""),
            getattr(t, "completion_date", ""),
            getattr(t, "duration", ""),
            getattr(t, "completion_percentage", 0) or 0,
            est, work, logged, difference,
            getattr(t, "billing_type", ""),
            getattr(t, "tags", ""),
            getattr(t, "description", ""),
            str(getattr(t, "created_at", "") or ""),
            str(getattr(t, "updated_at", "") or ""),
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=tasks_export.csv"}
    )



@router.get("/{task_id}", response_model=TaskResponse)
async def read_task(
    task_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_authenticated),
):
    db_task = await task_service.get_task(db, task_id=task_id)
    if db_task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    from app.core.security import get_user_view_level
    view_level = get_user_view_level(current_user, 'task-view')

    if view_level != 'All':
        from app.models.task import task_assignees, task_owners

        is_co_assignee = (await db.execute(
            select(exists().where(
                task_assignees.c.task_id == task_id,
                task_assignees.c.user_id == current_user.id
            ))
        )).scalar()

        is_co_owner = (await db.execute(
            select(exists().where(
                task_owners.c.task_id == task_id,
                task_owners.c.user_id == current_user.id
            ))
        )).scalar()

        has_access = (
            db_task.assignee_id == current_user.id or
            is_co_assignee or
            db_task.owner_id == current_user.id or
            is_co_owner or
            db_task.created_by_id == current_user.id
        )

        if not has_access and view_level == 'A':
            from app.models.project import ProjectMember, Project
            if db_task.project_id:
                is_member = (await db.execute(
                    select(Project.id).outerjoin(ProjectMember, ProjectMember.project_id == Project.id).where(
                        Project.id == db_task.project_id,
                        or_(
                            Project.owner_id == current_user.id,
                            Project.project_manager_id == current_user.id,
                            Project.delivery_head_id == current_user.id,
                            ProjectMember.user_id == current_user.id
                        )
                    )
                )).first() is not None

                if is_member:
                    has_access = True

        if not has_access:
            raise HTTPException(
                status_code=403,
                detail="Access denied: you are not assigned to this task and not a project member.",
            )
    return db_task


@router.put("/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: int,
    task: TaskUpdate,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(check_task_owner_or_lead),
):
    updated = await task_service.update_task(
        db,
        task_id=task_id,
        task_update=task,
        actor_id=current_user.o365_id or str(current_user.id),
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Task not found")
    await invalidate_cache("api:tasks_list")
    return updated


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_task_delete),
):
    success = await task_service.delete_task(
        db,
        task_id=task_id,
        actor_id=current_user.o365_id or str(current_user.id),
    )
    if not success:
        raise HTTPException(status_code=404, detail="Task not found")
    await invalidate_cache("api:tasks_list")
