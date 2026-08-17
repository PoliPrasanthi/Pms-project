from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.core.database import get_async_db
from app.core.security import allow_authenticated
from app.schemas.task_list import TaskListCreate, TaskListResponse, TaskListUpdate, TaskListListResponse
from app.services import task_list_service

router = APIRouter(dependencies=[Depends(allow_authenticated)])

@router.post("/", response_model=TaskListResponse)
async def create_task_list(
    task_list: TaskListCreate,
    db: AsyncSession = Depends(get_async_db),
    current_user = Depends(allow_authenticated)
):
    return await task_list_service.create_task_list(
        db=db, 
        task_list=task_list, 
        actor_id=current_user.o365_id or str(current_user.id),
        created_by_id=current_user.id
    )

@router.get("/search", response_model=List[TaskListResponse])
async def search_task_lists(
    q: str = Query(..., min_length=1),
    project_id: int = Query(None),
    limit: int = 20,
    db: AsyncSession = Depends(get_async_db)
):
    return await task_list_service.search_task_lists(db, query=q, project_id=project_id, limit=limit)

@router.get("/", response_model=TaskListListResponse)
async def read_task_lists(
    project_id: int = None,
    skip: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(get_async_db),
    current_user = Depends(allow_authenticated)
):
    from app.core.security import get_user_view_level
    view_level = get_user_view_level(current_user, 'task-view')
    return await task_list_service.get_task_lists(
        db,
        skip=skip,
        limit=limit,
        project_id=project_id,
        current_user=current_user if view_level not in ('All', None) else None,
        view_level=view_level,
    )

@router.get("/{task_list_id}", response_model=TaskListResponse)
async def read_task_list(
    task_list_id: int,
    db: AsyncSession = Depends(get_async_db)
):
    db_task_list = await task_list_service.get_task_list(db, task_list_id=task_list_id)
    if db_task_list is None:
        raise HTTPException(status_code=404, detail="Task List not found")
    return db_task_list

@router.put("/{task_list_id}", response_model=TaskListResponse)
async def update_task_list(
    task_list_id: int,
    task_list_in: TaskListUpdate,
    db: AsyncSession = Depends(get_async_db),
    current_user = Depends(allow_authenticated)
):
    custom = await task_list_service.update_task_list(db, task_list_id, task_list_in, actor_id=current_user.o365_id or str(current_user.id))
    if not custom:
        raise HTTPException(status_code=404, detail="Task List not found")
    return custom

@router.delete("/{task_list_id}", status_code=204)
async def delete_task_list(
    task_list_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user = Depends(allow_authenticated)
):
    success = await task_list_service.delete_task_list(db, task_list_id=task_list_id, actor_id=current_user.o365_id or str(current_user.id))
    if not success:
        raise HTTPException(status_code=404, detail="Task List not found")
