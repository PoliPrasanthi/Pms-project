from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.timesheet import Timesheet
from app.schemas.timesheet import TimesheetCreate, TimesheetUpdate
from app.utils.audit_utils import write_audit, capture_audit_details


async def get_timesheets(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
    project_id: int = None,
    user_email: str = None,
) -> list:
    stmt = select(Timesheet)
    if project_id:
        stmt = stmt.where(Timesheet.project_id == project_id)
    if user_email:
        stmt = stmt.where(Timesheet.user_email == user_email)
    return (await db.execute(stmt.offset(skip).limit(limit))).scalars().all()


async def get_timesheet(db: AsyncSession, timesheet_id: int) -> Optional[Timesheet]:
    return (await db.execute(select(Timesheet).where(Timesheet.id == timesheet_id))).scalar_one_or_none()


async def create_timesheet(
    db: AsyncSession,
    timesheet: TimesheetCreate,
    actor_id: Optional[str] = None,
) -> Timesheet:
    db_timesheet = Timesheet(
        name            = timesheet.name,
        start_date      = timesheet.start_date,
        end_date        = timesheet.end_date,
        project_id      = timesheet.project_id,
        user_email      = timesheet.user_email,
        billing_type    = timesheet.billing_type,
        total_hours     = timesheet.total_hours,
        approval_status = timesheet.approval_status,
    )
    db.add(db_timesheet)
    await db.flush()

    await write_audit(db, actor_id, "CREATE", "timesheets",
                resource_id=timesheet.project_id or db_timesheet.id,
                record_id=db_timesheet.id,
                details=[{"field_name": "name", "old_value": None, "new_value": timesheet.name}])

    await db.commit()
    await db.refresh(db_timesheet)
    return db_timesheet


async def update_timesheet(
    db: AsyncSession,
    timesheet_id: int,
    timesheet_update: TimesheetUpdate,
    actor_id: Optional[str] = None,
) -> Optional[Timesheet]:
    db_timesheet = (await db.execute(select(Timesheet).where(Timesheet.id == timesheet_id))).scalar_one_or_none()
    if not db_timesheet:
        return None

    update_data = timesheet_update.model_dump(exclude_unset=True)
    changes = capture_audit_details(db_timesheet, update_data)

    for key, value in update_data.items():
        setattr(db_timesheet, key, value)

    await write_audit(db, actor_id, "UPDATE", "timesheets",
                resource_id=db_timesheet.project_id or timesheet_id,
                record_id=timesheet_id,
                details=changes)

    await db.commit()
    await db.refresh(db_timesheet)
    return db_timesheet


async def delete_timesheet(
    db: AsyncSession,
    timesheet_id: int,
    actor_id: Optional[str] = None,
) -> bool:
    db_timesheet = (await db.execute(select(Timesheet).where(Timesheet.id == timesheet_id))).scalar_one_or_none()
    if not db_timesheet:
        return False

    await write_audit(db, actor_id, "DELETE", "timesheets",
                resource_id=db_timesheet.project_id or timesheet_id,
                record_id=timesheet_id,
                details=[{"field_name": "name", "old_value": db_timesheet.name, "new_value": None}])

    await db.delete(db_timesheet)
    await db.commit()
    return True
