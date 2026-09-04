from __future__ import annotations

from typing import List, Optional
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select, or_
from app.models.task import Task
from app.models.issue import Issue
from app.models.user import User
from app.core.database import get_async_db
from app.core.cache import endpoint_cache, invalidate_cache

from app.core.security import (
    allow_authenticated,
    allow_pm,
    allow_proj_create,
    allow_proj_view,
    check_project_owner_or_pm,
    check_project_owner_or_lead,
    is_employee_only,
    is_full_access,
)
from app.schemas.project import (
    ProjectCreate,
    ProjectUpdate,
    ProjectResponse,
    ProjectMemberCreate,
    ProjectMemberResponse,
    ProjectSyncUpdate,
    ProjectListResponse,
)
from app.schemas.task import TaskResponse, TaskListResponse
from app.schemas.issue import IssueResponse, IssueListResponse
from app.schemas.timelog import TimeLogResponse
from app.schemas.milestone import MilestoneResponse
from app.schemas.audit import AuditLogResponse
from app.schemas.template import TemplateCloneRequest, ProjectTemplateResponse
from app.services import project_service, task_service, issue_service, timelog_service, milestone_service
from app.services.template_cloning_service import TemplateCloningService

router = APIRouter(dependencies=[Depends(allow_authenticated)])

@router.post("/", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project_endpoint(
    project: ProjectCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_proj_create),
):
    if not project.owner_id:
        project.owner_id = current_user.id

    db_project = await project_service.create_project(
        db=db, project=project, actor_id=current_user.public_id
    )

    member_emails = project.user_emails or []

    def background_teams_worker(proj_name: str, emails: List[str], proj_id: int):
        from app.core.database import SessionLocal
        with SessionLocal() as db_session:
            from app.services.teams_automation import create_ms_team_for_project
            from sqlalchemy import select as sync_select
            from app.models.project import Project
            team_id = create_ms_team_for_project(proj_name, emails, proj_id)
            if team_id:
                proj = db_session.execute(sync_select(Project).where(Project.id == proj_id)).scalar_one_or_none()
                if proj:
                    proj.ms_teams_group_id = team_id
                    db_session.commit()

    background_tasks.add_task(
        background_teams_worker,
        proj_name  = db_project.project_name,
        emails     = member_emails,
        proj_id    = db_project.id,
    )
    await invalidate_cache(f"api:projects_list:usr_{current_user.id}")
    return db_project


@router.get("/check-sync-id")
async def check_sync_id(
    id: str = Query(..., min_length=1),
    exclude_project_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_async_db),
):
    try:
        from app.models.project import Project
        stmt = select(Project.id).where(Project.project_id_sync == id)
        if exclude_project_id:
            stmt = stmt.where(Project.id != exclude_project_id)
        exists = (await db.execute(stmt)).first() is not None
        return {"exists": exists}
    except Exception:
        logging.getLogger("app.projects").exception("check-sync-id failed for id=%s", id)
        raise HTTPException(status_code=503, detail="Unable to check sync ID availability. Please try again.")


@router.get("/check-name")
async def check_name(
    name: str = Query(..., min_length=1),
    exclude_project_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_async_db),
):
    try:
        from app.models.project import Project
        stmt = select(Project.id).where(Project.project_name == name)
        if exclude_project_id:
            stmt = stmt.where(Project.id != exclude_project_id)
        exists = (await db.execute(stmt)).first() is not None
        return {"exists": exists}
    except Exception:
        logging.getLogger("app.projects").exception("check-name failed for name=%s", name)
        raise HTTPException(status_code=503, detail="Unable to check name availability. Please try again.")


@router.get("/search", response_model=List[ProjectResponse])
async def search_projects(
    q: str = Query("", min_length=0), limit: int = 20, db: AsyncSession = Depends(get_async_db),
    current_user = Depends(allow_authenticated)
):
    from app.core.security import get_user_view_level
    view_level = get_user_view_level(current_user, 'proj-view')
    return await project_service.search_projects(
        db, query=q, limit=limit,
        current_user=current_user, view_level=view_level
    )


@router.get("/", response_model=ProjectListResponse)
@endpoint_cache(ttl=60, prefix="api:projects_list")
async def read_projects(
    skip: int = 0,
    limit: int = 100,
    is_archived: Optional[bool] = Query(None),
    is_template: Optional[bool] = Query(None),
    include_all: bool = Query(True),
    status_id: Optional[List[int]] = Query(None),
    priority_id: Optional[List[int]] = Query(None),
    manager_emails: Optional[List[str]] = Query(None),
    member_email: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    sort_by: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_proj_view),
):
    from app.core.security import get_user_view_level
    view_level = get_user_view_level(current_user, 'proj-view')

    return await project_service.get_projects(
        db,
        skip=skip,
        limit=limit,
        status_ids=status_id,
        priority_ids=priority_id,
        manager_emails=manager_emails,
        member_email=member_email,
        is_archived=is_archived,
        is_template=is_template,
        include_all=include_all,
        search=search,
        current_user=current_user if view_level not in ('All', None) else None,
        view_level=view_level,
    )


@router.get("/export")
async def export_projects(
    is_archived: Optional[bool] = Query(None),
    is_template: Optional[bool] = Query(None),
    include_all: bool = Query(True),
    status_id: Optional[List[int]] = Query(None),
    priority_id: Optional[List[int]] = Query(None),
    manager_emails: Optional[List[str]] = Query(None),
    member_email: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_proj_view),
):
    from app.core.security import get_user_view_level
    import csv
    import io
    from fastapi.responses import StreamingResponse

    view_level = get_user_view_level(current_user, 'proj-view')

    data = await project_service.get_projects(
        db,
        skip=0,
        limit=1000000,
        status_ids=status_id,
        priority_ids=priority_id,
        manager_emails=manager_emails,
        member_email=member_email,
        is_archived=is_archived,
        is_template=is_template,
        include_all=include_all,
        search=search,
        current_user=current_user if view_level not in ('All', None) else None,
        view_level=view_level,
        return_raw=True
    )

    async def iter_csv():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Project ID", "Project Name", "Customer Name", "Client Name",
            "Billing Model", "Project Type", "Status", "Priority",
            "Owner", "Project Manager", "Delivery Head", "Team",
            "Expected Start", "Expected End", "Actual Start", "Actual End",
            "Estimated Hours", "Actual Hours", "Completion %",
            "Tags", "Description", "Is Archived", "Is Template",
            "Created At", "Updated At"
        ])
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)

        for p in data.get("items", []):
            owner = getattr(p, "owner", None)
            pm = getattr(p, "project_manager", None)
            dh = getattr(p, "delivery_head", None)
            
            owner_name = f"{getattr(owner, 'first_name', '')} {getattr(owner, 'last_name', '')}".strip() if owner else ""
            pm_name = f"{getattr(pm, 'first_name', '')} {getattr(pm, 'last_name', '')}".strip() if pm else ""
            dh_name = f"{getattr(dh, 'first_name', '')} {getattr(dh, 'last_name', '')}".strip() if dh else ""

            status_master = getattr(p, "status_master", None)
            priority_master = getattr(p, "priority_master", None)

            # Build team members list
            team_members = getattr(p, "team_members", []) or []
            member_names = []
            for m in team_members:
                u = getattr(m, "user", None)
                if u:
                    member_names.append(f"{getattr(u, 'first_name', '')} {getattr(u, 'last_name', '')}".strip())
            team_str = ", ".join(member_names) if member_names else ""

            writer.writerow([
                getattr(p, "public_id", ""),
                getattr(p, "project_name", ""),
                getattr(p, "customer_name", ""),
                getattr(p, "client_name", ""),
                getattr(p, "billing_model", ""),
                getattr(p, "project_type", ""),
                getattr(status_master, "label", "") if status_master else "",
                getattr(priority_master, "label", "") if priority_master else "",
                owner_name, pm_name, dh_name, team_str,
                getattr(p, "expected_start_date", ""),
                getattr(p, "expected_end_date", ""),
                getattr(p, "actual_start_date", ""),
                getattr(p, "actual_end_date", ""),
                getattr(p, "estimated_hours", 0) or 0,
                getattr(p, "actual_hours", 0) or 0,
                getattr(p, "completion_percentage", 0) or 0,
                getattr(p, "tags", ""),
                getattr(p, "description", ""),
                "Yes" if getattr(p, "is_archived", False) else "No",
                "Yes" if getattr(p, "is_template", False) else "No",
                str(getattr(p, "created_at", "") or ""),
                str(getattr(p, "updated_at", "") or ""),
            ])
            
            if output.tell() > 4096:
                yield output.getvalue()
                output.seek(0)
                output.truncate(0)

        if output.tell() > 0:
            yield output.getvalue()

    return StreamingResponse(
        iter_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=projects_export.csv"}
    )



@router.get("/{project_id}", response_model=ProjectResponse)
async def read_project(
    project_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_proj_view),
):
    db_project = await project_service.get_project(db, project_id=project_id)
    if db_project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    if not is_full_access(current_user):
        member_ids = {m.user_id for m in db_project.team_members}
        is_owner = db_project.owner_id == current_user.id
        is_pm = db_project.project_manager_id == current_user.id
        is_dh = db_project.delivery_head_id == current_user.id

        has_access = (
            current_user.id in member_ids
            or is_owner
            or is_pm
            or is_dh
        )

        if not has_access:
            is_task_related = (await db.execute(
                select(Task.id).where(
                    Task.project_id == project_id,
                    or_(
                        Task.assignee_id == current_user.id,
                        Task.created_by_id == current_user.id,
                    )
                )
            )).first() is not None

            if not is_task_related:
                from app.models.task import task_assignees
                is_task_assoc = (await db.execute(
                    select(task_assignees.c.task_id).join(Task, Task.id == task_assignees.c.task_id).where(
                        Task.project_id == project_id,
                        task_assignees.c.user_id == current_user.id
                    )
                )).first() is not None
                if is_task_assoc:
                    is_task_related = True

            if is_task_related:
                has_access = True

        if not has_access:
            is_issue_related = (await db.execute(
                select(Issue.id).where(
                    Issue.project_id == project_id,
                    or_(
                        Issue.assignee_id == current_user.id,
                        Issue.reporter_id == current_user.id,
                    )
                )
            )).first() is not None

            if not is_issue_related:
                from app.models.issue import issue_assignees
                is_issue_assoc = (await db.execute(
                    select(issue_assignees.c.issue_id).join(Issue, Issue.id == issue_assignees.c.issue_id).where(
                        Issue.project_id == project_id,
                        issue_assignees.c.user_id == current_user.id
                    )
                )).first() is not None
                if is_issue_assoc:
                    is_issue_related = True

            if is_issue_related:
                has_access = True

        if not has_access:
            raise HTTPException(
                status_code=403,
                detail="You are not a member or assignee of this project.",
            )

    return db_project


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: int,
    project: ProjectUpdate,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(check_project_owner_or_lead),
):
    db_project = await project_service.update_project(
        db,
        project_id     = project_id,
        project_update = project,
        actor_id       = current_user.o365_id or str(current_user.id),
    )
    if db_project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    await invalidate_cache(f"api:projects_list:usr_{current_user.id}")
    return db_project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(check_project_owner_or_pm),
):
    success = await project_service.delete_project(
        db,
        project_id = project_id,
        actor_id   = current_user.o365_id or str(current_user.id),
    )
    if not success:
        raise HTTPException(status_code=404, detail="Project not found")
    await invalidate_cache(f"api:projects_list:usr_{current_user.id}")


@router.patch("/{project_id}/archive", response_model=ProjectResponse)
async def archive_project(
    project_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(check_project_owner_or_pm),
):
    result = await project_service.archive_project(
        db,
        project_id = project_id,
        archived   = True,
        actor_id   = current_user.o365_id or str(current_user.id),
    )
    if not result:
        raise HTTPException(status_code=404, detail="Project not found")
    await invalidate_cache(f"api:projects_list:usr_{current_user.id}")
    return result


@router.patch("/{project_id}/unarchive", response_model=ProjectResponse)
async def unarchive_project(
    project_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(check_project_owner_or_pm),
):
    result = await project_service.archive_project(
        db,
        project_id = project_id,
        archived   = False,
        actor_id   = current_user.o365_id or str(current_user.id),
    )
    if not result:
        raise HTTPException(status_code=404, detail="Project not found")
    await invalidate_cache(f"api:projects_list:usr_{current_user.id}")
    return result


@router.post("/{project_id}/sync", response_model=ProjectResponse)
async def sync_project(
    project_id: int,
    sync_data: ProjectSyncUpdate,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_proj_create),
):
    result = await project_service.sync_project_fields(
        db,
        project_id = project_id,
        sync_data  = sync_data,
        actor_id   = current_user.o365_id or str(current_user.id),
    )
    if not result:
        raise HTTPException(status_code=404, detail="Project not found")
    return result


@router.get("/{project_id}/members", response_model=List[ProjectMemberResponse])
async def get_project_members(
    project_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_authenticated),
):
    db_project = await project_service.get_project(db, project_id=project_id)
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    return db_project.team_members


@router.post("/{project_id}/members", response_model=ProjectMemberResponse, status_code=status.HTTP_201_CREATED)
async def add_project_member(
    project_id: int,
    member: ProjectMemberCreate,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(check_project_owner_or_pm),
):
    user = None
    if member.user_id:
        user = (await db.execute(select(User).where(User.id == member.user_id))).scalar_one_or_none()
    elif member.user_email:
        user = (await db.execute(select(User).where(User.email == member.user_email))).scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    result = await project_service.add_project_member(
        db,
        project_id      = project_id,
        user_id         = user.id,
        project_profile = member.project_profile,
        portal_profile  = member.portal_profile,
    )
    return result


@router.delete("/{project_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_project_member(
    project_id: int,
    user_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(check_project_owner_or_pm),
):
    db_project = await project_service.get_project(db, project_id=project_id)
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")

    success = await project_service.remove_project_member(
        db,
        project_id = project_id,
        user_id    = user_id,
        owner_id   = db_project.owner_id,
    )
    if not success:
        raise HTTPException(
            status_code=400,
            detail="Cannot remove this member. They may not be on the project or they are the project owner.",
        )


@router.get("/{project_id}/audit", response_model=List[AuditLogResponse])
async def get_project_audit_logs(
    project_id: int,
    db: AsyncSession = Depends(get_async_db),
):
    from app.models.audit import AuditLogs

    result = await db.execute(
        select(AuditLogs)
        .where(
            AuditLogs.TableName == "projects",
            AuditLogs.Comments.like(f"%Record ID: {project_id}%"),
        )
        .order_by(AuditLogs.PerformedOn.desc())
    )
    return result.scalars().all()


@router.get("/{project_id}/dashboard")
async def get_project_dashboard(
    project_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_authenticated),
):
    db_project = await project_service.get_project(db, project_id=project_id)
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")

    return {
        "project_id": project_id,
        "counts": {
            "task_count": getattr(db_project, "task_count", 0),
            "issue_count": getattr(db_project, "issue_count", 0),
            "milestone_count": getattr(db_project, "milestone_count", 0),
            "completion_percentage": getattr(db_project, "completion_percentage", 0),
        }
    }


@router.get("/{project_id}/tasks", response_model=TaskListResponse)
async def get_project_tasks(
    project_id: int,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_authenticated),
):
    return await task_service.get_tasks(db, project_id=project_id, skip=skip, limit=limit)


@router.get("/{project_id}/issues", response_model=IssueListResponse)
async def get_project_issues(
    project_id: int,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_authenticated),
):
    return await issue_service.get_issues(db, project_id=project_id, skip=skip, limit=limit)


@router.get("/{project_id}/timelogs", response_model=List[TimeLogResponse])
async def get_project_timelogs(
    project_id: int,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_authenticated),
):
    return await timelog_service.get_timelogs(db, project_id=project_id, skip=skip, limit=limit)


@router.get("/{project_id}/milestones", response_model=List[MilestoneResponse])
async def get_project_milestones(
    project_id: int,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_authenticated),
):
    return await milestone_service.get_milestones(db, project_id=project_id, skip=skip, limit=limit)


@router.post("/{project_id}/clone-to-template", response_model=ProjectTemplateResponse, status_code=status.HTTP_201_CREATED)
async def clone_project_to_template(
    project_id: int,
    request: TemplateCloneRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_authenticated),
):
    new_template = await TemplateCloningService.clone_project_to_template(
        db=db,
        project_id=project_id,
        request=request,
        user_id=current_user.id,
    )
    return new_template
