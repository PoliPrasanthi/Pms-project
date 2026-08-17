from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional

from app.core.database import get_async_db
from app.core.security import allow_authenticated, allow_time_delete, is_employee_only, is_full_access, allow_time_create, allow_time_view
from app.core.dependencies import auto_populate_timelog
from app.schemas.timelog import TimeLogCreate, TimeLogUpdate, TimeLogResponse, TimeLogBulkCreate, TimeLogListResponse
from app.services import timelog_service

router = APIRouter(dependencies=[Depends(allow_authenticated)])


@router.post("/", response_model=TimeLogResponse)
async def create_timelog(
    timelog: TimeLogCreate,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_time_create),
):
    auto_populate_timelog(timelog, current_user)
    if not is_full_access(current_user):
        timelog.user_id = current_user.id

    return await timelog_service.create_timelog(
        db=db,
        timelog=timelog,
        actor_id=current_user.o365_id or str(current_user.id),
        created_by_id=current_user.id,
    )


@router.post("/bulk", response_model=List[TimeLogResponse])
async def create_timelogs_bulk(
    bulk: TimeLogBulkCreate,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_time_create),
):
    for log in bulk.logs:
        auto_populate_timelog(log, current_user)
        if not is_full_access(current_user):
            log.user_id = current_user.id

    return await timelog_service.create_timelogs_bulk(
        db=db,
        timelogs=bulk.logs,
        actor_id=current_user.o365_id or str(current_user.id),
        created_by_id=current_user.id,
    )


@router.get("/", response_model=TimeLogListResponse)
async def read_timelogs(
    project_id: Optional[int] = None,
    task_id: Optional[int] = None,
    issue_id: Optional[int] = None,
    user_id: Optional[List[int]] = Query(None),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    current_user=Depends(allow_time_view),
    db: AsyncSession = Depends(get_async_db),
):
    from app.core.security import get_user_view_level
    view_level = get_user_view_level(current_user, 'time-view')
    
    return await timelog_service.get_timelogs(
        db,
        skip=skip,
        limit=limit,
        project_id=project_id,
        task_id=task_id,
        issue_id=issue_id,
        user_ids=user_id,
        start_date=start_date,
        end_date=end_date,
        current_user=current_user if view_level not in ('All', None) else None,
        view_level=view_level,
    )


@router.get("/export")
async def export_timelogs(
    project_id: Optional[int] = None,
    task_id: Optional[int] = None,
    issue_id: Optional[int] = None,
    user_id: Optional[List[int]] = Query(None),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user=Depends(allow_time_view),
    db: AsyncSession = Depends(get_async_db),
):
    from app.core.security import get_user_view_level
    import csv
    import io
    from fastapi.responses import StreamingResponse

    view_level = get_user_view_level(current_user, 'time-view')
    
    data = await timelog_service.get_timelogs(
        db,
        skip=0,
        limit=1000000,
        project_id=project_id,
        task_id=task_id,
        issue_id=issue_id,
        user_ids=user_id,
        start_date=start_date,
        end_date=end_date,
        current_user=current_user if view_level not in ('All', None) else None,
        view_level=view_level,
    )

    async def iter_csv():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Timelog ID", "User", "Project", "Task", "Issue", "Log Title",
            "Log Date", "Hours", "Time Period", "Is Billable",
            "Approval Status", "Created By", "Notes", "Created At"
        ])
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)

        for t in data.get("items", []):
            u = getattr(t, "user", None)
            user_name = f"{getattr(u, 'first_name', '')} {getattr(u, 'last_name', '')}".strip() if u else ""

            proj = getattr(t, "project", None)
            task = getattr(t, "task", None)
            issue = getattr(t, "issue", None)
            
            cb = getattr(t, "created_by", None)
            created_by_name = f"{getattr(cb, 'first_name', '')} {getattr(cb, 'last_name', '')}".strip() if cb else ""

            approval = getattr(t, "approval_status_master", None)
            approval_label = getattr(approval, "label", "") if approval else ""

            writer.writerow([
                getattr(t, "public_id", ""),
                user_name,
                getattr(proj, "project_name", "") if proj else "",
                getattr(task, "task_name", "") if task else "",
                getattr(issue, "bug_name", "") if issue else "",
                getattr(t, "log_title", ""),
                str(getattr(t, "date", "") or ""),
                getattr(t, "daily_log_hours", 0) or 0,
                getattr(t, "time_period", ""),
                "Yes" if getattr(t, "billing_type", "") == "Billable" else "No",
                approval_label,
                created_by_name,
                getattr(t, "notes", ""),
                str(getattr(t, "created_at", "") or ""),
            ])
            
            # Yield every 100 rows to prevent memory build-up and timeout
            if output.tell() > 4096:
                yield output.getvalue()
                output.seek(0)
                output.truncate(0)
                
        # Yield remaining buffer
        if output.tell() > 0:
            yield output.getvalue()

    return StreamingResponse(
        iter_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=timelogs_export.csv"}
    )



@router.get("/{timelog_id}", response_model=TimeLogResponse)
async def read_timelog(
    timelog_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_time_view),
):
    db_timelog = await timelog_service.get_timelog(db, timelog_id=timelog_id)
    if db_timelog is None:
        raise HTTPException(status_code=404, detail="TimeLog not found")

    if is_employee_only(current_user) and db_timelog.user_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Access denied: you can only view your own time logs.",
        )
    return db_timelog


@router.put("/{timelog_id}", response_model=TimeLogResponse)
async def update_timelog(
    timelog_id: int,
    timelog: TimeLogUpdate,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_time_view),
):
    db_timelog = await timelog_service.get_timelog(db, timelog_id=timelog_id)
    if db_timelog is None:
        raise HTTPException(status_code=404, detail="TimeLog not found")
    if is_employee_only(current_user) and db_timelog.user_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Access denied: you can only update your own time logs.",
        )
    updated = await timelog_service.update_timelog(
        db,
        timelog_id=timelog_id,
        timelog_update=timelog,
        actor_id=current_user.o365_id or str(current_user.id),
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="TimeLog not found")
    return updated


@router.delete("/{timelog_id}", status_code=204)
async def delete_timelog(
    timelog_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_time_view),
):
    db_timelog = await timelog_service.get_timelog(db, timelog_id=timelog_id)
    if db_timelog is None:
        raise HTTPException(status_code=404, detail="TimeLog not found")
        
    if is_employee_only(current_user) and db_timelog.user_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Access denied: you can only delete your own time logs.",
        )
        
    success = await timelog_service.delete_timelog(
        db,
        timelog_id=timelog_id,
        actor_id=current_user.o365_id or str(current_user.id),
    )
    if not success:
        raise HTTPException(status_code=404, detail="TimeLog not found")
