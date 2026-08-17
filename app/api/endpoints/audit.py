from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from app.core.database import get_async_db
from app.core.security import allow_pm
from app.models.audit import AuditLogs
from app.schemas.audit import AuditLogResponse

router = APIRouter(dependencies=[Depends(allow_pm)])

@router.get("/", response_model=List[AuditLogResponse])
async def read_audit_logs(
    skip: int = 0,
    limit: int = 100,
    resource_name: Optional[str] = None,
    user_id: Optional[str] = None,
    db: AsyncSession = Depends(get_async_db)
):
    from sqlalchemy import select
    query = select(AuditLogs)
    if resource_name:
        query = query.where(AuditLogs.TableName == resource_name)
    if user_id:
        query = query.where(AuditLogs.PerformedBy == user_id)

    query = query.order_by(AuditLogs.PerformedOn.desc()).offset(skip).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()
