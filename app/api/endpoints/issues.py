from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional

from app.core.database import get_async_db
from app.core.cache import endpoint_cache, invalidate_cache
from sqlalchemy import select, or_, exists
from app.core.security import allow_authenticated, is_employee_only, is_full_access, allow_issue_create, allow_issue_view, allow_issue_delete, check_issue_owner_or_lead

from app.models.user import User
from app.schemas.user import UserBase

from app.schemas.issue import IssueCreate, IssueUpdate, IssueResponse, IssueListResponse
from app.services import issue_service

router = APIRouter(dependencies=[Depends(allow_authenticated)])


@router.post("/", response_model=IssueResponse)
async def create_issue(
    issue: IssueCreate,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_issue_create),
):
    if not issue.reporter_id:
        issue.reporter_id = current_user.id
    result = await issue_service.create_issue(
        db=db,
        issue=issue,
        actor_id=current_user.o365_id or str(current_user.id),
        created_by_id=current_user.id,
    )
    await invalidate_cache("api:issues_list")
    return result


@router.post("/bulk", response_model=List[IssueResponse])
async def bulk_create_issues(
    issues: List[IssueCreate],
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_issue_create),
):
    for i in issues:
        if not i.reporter_id:
            i.reporter_id = current_user.id
    result = await issue_service.bulk_create_issues(
        db=db, issues=issues,
        actor_id=current_user.o365_id or str(current_user.id),
        created_by_id=current_user.id,
    )
    await invalidate_cache("api:issues_list")
    return result


@router.get("/assignees", response_model=List[UserBase])
@endpoint_cache(ttl=60, prefix="api:issues_assignees")
async def get_active_issue_assignees(
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_authenticated),
):
    from app.core.security import get_user_view_level
    view_level = get_user_view_level(current_user, 'issue-view')
    users = await issue_service.get_active_issue_assignees(
        db, current_user=current_user, view_level=view_level
    )
    return [UserBase.model_validate(u) for u in users]


@router.get("/search", response_model=List[IssueResponse])
@endpoint_cache(ttl=60, prefix="api:issues_list")
async def search_issues(
    q: str = Query(..., min_length=1),
    project_id: Optional[int] = Query(None),
    limit: int = 20,
    db: AsyncSession = Depends(get_async_db),
):
    return await issue_service.search_issues(db, query=q, project_id=project_id, limit=limit)


@router.get("/", response_model=IssueListResponse)
@endpoint_cache(ttl=60, prefix="api:issues_list")
async def read_issues(
    skip: int = 0,
    limit: int = 100,
    project_id: Optional[int] = None,
    status_id: Optional[List[int]] = Query(None),
    priority_id: Optional[List[int]] = Query(None),
    severity_id: Optional[List[int]] = Query(None),
    assignee_email: Optional[List[str]] = Query(None),
    search: Optional[str] = None,
    sort_by: Optional[str] = Query(None),
    milestone_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_issue_view),
):
    from app.core.security import get_user_view_level
    view_level = get_user_view_level(current_user, 'issue-view')

    # The RBAC and view_level filtering is entirely handled in issue_service.py now.

    return await issue_service.get_issues(
        db,
        skip=skip,
        limit=limit,
        project_id=project_id,
        status_ids=status_id,
        priority_ids=priority_id,
        severity_ids=severity_id,
        assignee_emails=assignee_email,
        search=search,
        sort_by=sort_by,
        milestone_id=milestone_id,
        current_user=current_user,
        view_level=view_level,
    )


@router.get("/export")
async def export_issues(
    project_id: Optional[int] = None,
    status_id: Optional[List[int]] = Query(None),
    priority_id: Optional[List[int]] = Query(None),
    severity_id: Optional[List[int]] = Query(None),
    assignee_email: Optional[List[str]] = Query(None),
    search: Optional[str] = None,
    sort_by: Optional[str] = Query(None),
    milestone_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_issue_view),
):
    from app.core.security import get_user_view_level
    import csv
    import io
    from fastapi.responses import StreamingResponse

    view_level = get_user_view_level(current_user, 'issue-view')

    # The RBAC and view_level filtering is entirely handled in issue_service.py now.

    data = await issue_service.get_issues(
        db,
        skip=0,
        limit=1000000,
        project_id=project_id,
        status_ids=status_id,
        priority_ids=priority_id,
        severity_ids=severity_id,
        assignee_emails=assignee_email,
        search=search,
        sort_by=sort_by,
        milestone_id=milestone_id,
        current_user=current_user,
        view_level=view_level,
        return_raw=True
    )

    async def iter_csv():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Issue ID", "Bug Name", "Project", "Milestone",
            "Status", "Priority", "Severity", "Classification", "Module",
            "Assignee", "Co-Assignees", "Reporter", "Followers", "Reproducible", "Flag",
            "Associated Team", "Start Date", "Due Date", "Estimated Hours",
            "Tags", "Description", "Created At", "Updated At"
        ])
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)

        for i in data.get("items", []):
            assignee = getattr(i, "assignee", None)
            reporter = getattr(i, "reporter", None)
            
            assignee_name = f"{getattr(assignee, 'first_name', '')} {getattr(assignee, 'last_name', '')}".strip() if assignee else ""
            reporter_name = f"{getattr(reporter, 'first_name', '')} {getattr(reporter, 'last_name', '')}".strip() if reporter else ""
            
            # Relationships
            proj = getattr(i, "project", None)
            ms = getattr(i, "milestone", None)
            status_m = getattr(i, "status_master", None)
            priority_m = getattr(i, "priority_master", None)
            severity_m = getattr(i, "severity_master", None)
            class_m = getattr(i, "classification_master", None)
            team = getattr(i, "associated_team", None)

            writer.writerow([
                getattr(i, "public_id", ""),
                getattr(i, "bug_name", ""),
                getattr(proj, "project_name", "") if proj else "",
                getattr(ms, "name", "") if ms else "",
                getattr(status_m, "label", "") if status_m else "",
                getattr(priority_m, "label", "") if priority_m else "",
                getattr(severity_m, "label", "") if severity_m else "",
                getattr(class_m, "label", "") if class_m else "",
                getattr(i, "module", ""),
                assignee_name,
                "", # Co-assignees skipped for flat CSV mapping
                reporter_name,
                "", # Followers skipped
                "Yes" if getattr(i, "reproducible_flag", False) else "No",
                getattr(i, "flag", ""),
                getattr(team, "name", "") if team else "",
                str(getattr(i, "start_date", "") or ""),
                str(getattr(i, "due_date", "") or ""),
                getattr(i, "estimated_hours", 0) or 0,
                getattr(i, "tags", ""),
                getattr(i, "description", ""),
                str(getattr(i, "created_at", "") or ""),
                str(getattr(i, "updated_at", "") or ""),
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
        headers={"Content-Disposition": "attachment; filename=issues_export.csv"}
    )



@router.get("/{issue_id}", response_model=IssueResponse)
async def read_issue(
    issue_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_issue_view),
):
    db_issue = await issue_service.get_issue(db, issue_id=issue_id)
    if db_issue is None:
        raise HTTPException(status_code=404, detail="Issue not found")

    from app.core.security import get_user_view_level
    view_level = get_user_view_level(current_user, 'issue-view')

    if view_level != 'All':
        from app.models.issue import issue_assignees

        is_co_assignee = (await db.execute(
            select(exists().where(
                issue_assignees.c.issue_id == issue_id,
                issue_assignees.c.user_id == current_user.id
            ))
        )).scalar()

        has_access = (
            db_issue.reporter_id == current_user.id or
            db_issue.assignee_id == current_user.id or
            is_co_assignee
        )

        if not has_access and view_level == 'A':
            from app.models.project import ProjectMember, Project
            if db_issue.project_id:
                is_member = (await db.execute(
                    select(Project.id).outerjoin(ProjectMember, ProjectMember.project_id == Project.id).where(
                        Project.id == db_issue.project_id,
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
                detail="Access denied: you are not assigned to this issue and not a project member.",
            )
    return db_issue


@router.put("/{issue_id}", response_model=IssueResponse)
async def update_issue(
    issue_id: int,
    issue: IssueUpdate,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(check_issue_owner_or_lead),
):
    db_issue = await issue_service.get_issue(db, issue_id=issue_id)
    if db_issue is None:
        raise HTTPException(status_code=404, detail="Issue not found")

    updated = await issue_service.update_issue(
        db,
        issue_id=issue_id,
        issue_update=issue,
        actor_id=current_user.o365_id or str(current_user.id),
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Issue not found")
    await invalidate_cache("api:issues_list")
    return updated


@router.delete("/{issue_id}", status_code=204)
async def delete_issue(
    issue_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_issue_delete),
):
    success = await issue_service.delete_issue(
        db,
        issue_id=issue_id,
        actor_id=current_user.o365_id or str(current_user.id),
    )
    if not success:
        raise HTTPException(status_code=404, detail="Issue not found")
    await invalidate_cache("api:issues_list")
