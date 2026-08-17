from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_db
from app.core.security import get_current_user, allow_authenticated, is_employee_only, allow_user_view, allow_user_create, allow_user_edit, allow_user_delete
from app.models.user import User
from app.schemas.user import UserCreate, UserResponse, UserUpdate, UserListResponse
from app.services import user_service

router = APIRouter(dependencies=[Depends(allow_authenticated)])

@router.get("/me", response_model=UserResponse)
async def read_user_me(current_user: User = Depends(get_current_user)):
    return current_user

@router.get("/search", response_model=List[UserResponse])
async def search_users(
    q: Optional[str] = Query(None),
    limit: int = 20,
    is_active: Optional[bool] = Query(True),
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_user_view)
):
    return await user_service.search_users(db, query=q, limit=limit, is_active=is_active)

@router.post("/", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    user: UserCreate,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_user_create),
):
    if await user_service.get_user_by_email(db, email=user.email):
        raise HTTPException(status_code=400, detail="Email already registered")
    if user.username and await user_service.get_user_by_username(db, username=user.username):
        raise HTTPException(status_code=400, detail="Username already registered")
    return await user_service.create_user(db=db, user=user, actor_id=current_user.o365_id or str(current_user.id))

@router.post("/sync", status_code=status.HTTP_202_ACCEPTED)
async def sync_users_from_azure(
    background_tasks: BackgroundTasks,
    current_user=Depends(allow_user_create)
):
    from app.services.graph_service import sync_all_users_task
    background_tasks.add_task(sync_all_users_task)
    return {"message": "Azure AD sync started in the background."}

@router.get("/", response_model=UserListResponse)
async def read_users(
    skip: int = 0,
    limit: int = Query(default=20, ge=1, le=500, description="Maximum 500 users per page. Use skip for pagination."),
    search: Optional[str] = None,
    role_id: Optional[List[int]] = Query(None),
    is_active: Optional[bool] = Query(None),
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_user_view)
):
    return await user_service.get_users(db, skip=skip, limit=limit, search=search, role_ids=role_id, is_active=is_active)

@router.get("/{user_id}", response_model=UserResponse)
async def read_user(
    user_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_user_view)
):
    db_user = await user_service.get_user(db, user_id=user_id)
    if db_user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return db_user

@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    user_update: UserUpdate,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_user_edit),
):
    if is_employee_only(current_user) and current_user.id != user_id:
        raise HTTPException(status_code=403, detail="Access denied: you can only update your own profile.")
    db_user = await user_service.update_user(
        db, user_id=user_id, user_update=user_update, actor_id=current_user.o365_id or str(current_user.id)
    )
    if db_user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return db_user

@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_user_delete),
):
    success = await user_service.delete_user(db, user_id=user_id, actor_id=current_user.o365_id or str(current_user.id))
    if not success:
        raise HTTPException(status_code=404, detail="User not found")
