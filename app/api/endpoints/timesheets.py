from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.core.database import get_async_db
from app.core.security import allow_authenticated, allow_time_delete, is_employee_only
from app.schemas.timesheet import TimesheetCreate, TimesheetUpdate, TimesheetResponse
from app.services import timesheet_service

router = APIRouter(dependencies=[Depends(allow_authenticated)])

@router.post("/", response_model=TimesheetResponse)
async def create_timesheet(timesheet: TimesheetCreate, db: AsyncSession = Depends(get_async_db), current_user = Depends(allow_authenticated)):
    if is_employee_only(current_user):
        timesheet.user_email = current_user.email
    return await timesheet_service.create_timesheet(db=db, timesheet=timesheet, actor_id=current_user.o365_id or str(current_user.id))

@router.get("/", response_model=List[TimesheetResponse])
async def read_timesheets(
    skip: int = 0,
    limit: int = 100,
    project_id: int = None,
    user_email: str = None,
    db: AsyncSession = Depends(get_async_db),
    current_user = Depends(allow_authenticated)
):
    from app.core.security import get_user_view_level
    view_level = get_user_view_level(current_user, 'time-view')
    
    if view_level in ('O', 'A'):
        user_email = current_user.email
    return await timesheet_service.get_timesheets(db, skip=skip, limit=limit, project_id=project_id, user_email=user_email)

@router.get("/{timesheet_id}", response_model=TimesheetResponse)
async def read_timesheet(timesheet_id: int, db: AsyncSession = Depends(get_async_db), current_user = Depends(allow_authenticated)):
    db_timesheet = await timesheet_service.get_timesheet(db, timesheet_id=timesheet_id)
    if db_timesheet is None:
        raise HTTPException(status_code=404, detail="Timesheet not found")
    if is_employee_only(current_user) and db_timesheet.user_email != current_user.email:
        raise HTTPException(status_code=403, detail="Access denied: you can only view your own timesheets.")
    return db_timesheet

@router.put("/{timesheet_id}", response_model=TimesheetResponse)
async def update_timesheet(timesheet_id: int, timesheet: TimesheetUpdate, db: AsyncSession = Depends(get_async_db), current_user = Depends(allow_authenticated)):
    db_timesheet = await timesheet_service.get_timesheet(db, timesheet_id=timesheet_id)
    if db_timesheet is None:
        raise HTTPException(status_code=404, detail="Timesheet not found")
    if is_employee_only(current_user) and db_timesheet.user_email != current_user.email:
        raise HTTPException(status_code=403, detail="Access denied: you can only update your own timesheets.")

    updated = await timesheet_service.update_timesheet(db, timesheet_id=timesheet_id, timesheet_update=timesheet, actor_id=current_user.o365_id or str(current_user.id))
    if updated is None:
        raise HTTPException(status_code=404, detail="Timesheet not found")
    return updated

@router.delete("/{timesheet_id}", status_code=204, dependencies=[Depends(allow_time_delete)])
async def delete_timesheet(timesheet_id: int, db: AsyncSession = Depends(get_async_db), current_user = Depends(allow_time_delete)):
    success = await timesheet_service.delete_timesheet(db, timesheet_id=timesheet_id, actor_id=current_user.o365_id or str(current_user.id))
    if not success:
        raise HTTPException(status_code=404, detail="Timesheet not found")
