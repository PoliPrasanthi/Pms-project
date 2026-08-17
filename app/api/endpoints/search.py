from typing import Any, List
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_async_db
from app.core.security import allow_authenticated
from app.services.search_service import search_service

router = APIRouter(dependencies=[Depends(allow_authenticated)])

@router.get("/", response_model=List[Any])
async def global_search(
    db: AsyncSession = Depends(get_async_db),
    q: str = Query(..., min_length=1),
    limit: int = Query(15, gt=0, le=100),
    current_user = Depends(allow_authenticated)
) -> Any:
    return await search_service.global_search(db, query=q, limit=limit, current_user=current_user)

@router.get("/work-items", response_model=List[Any])
async def search_work_items(
    db: AsyncSession = Depends(get_async_db),
    q: str = Query("", min_length=0),
    project_id: int = Query(None),
    limit: int = Query(50, gt=0, le=100),
    assignee_me: bool = Query(False),
    exclude_completed: bool = Query(False),
    current_user = Depends(allow_authenticated)
) -> Any:
    return await search_service.search_work_items(
        db, query=q, project_id=project_id, limit=limit, 
        current_user=current_user, assignee_me=assignee_me,
        exclude_completed=exclude_completed
    )
