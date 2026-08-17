from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.core.database import get_async_db
from app.core.security import allow_authenticated, allow_role_manage
from app.schemas.masters import MasterResponse, RoleResponse, SkillResponse, RoleCreate, RoleUpdate, MasterLookupResponse, BulkRolePermissionsUpdate
from app.schemas.user import RoleWithUsersResponse
from app.services import master_service
from app.core.cache import cache

router = APIRouter(dependencies=[Depends(allow_authenticated)])

@router.get("/lookups/{category}", response_model=List[MasterLookupResponse])
async def read_master_lookups(category: str, db: AsyncSession = Depends(get_async_db)):
    clean_cat = category.strip().lower()
    cache_key = f"master_lookup:{clean_cat}"
    cached_data = await cache.get(cache_key)
    if cached_data:
        return cached_data

    from app.models.master import MasterLookup
    from sqlalchemy import select, func, or_
    
    categories_to_check = [category]
    if clean_cat in ["taskstatus", "task_status", "status", "task status"]:
        categories_to_check = ["TaskStatus", "task_status", "Status", "task status"]
    elif clean_cat in ["taskpriority", "task_priority", "priority", "task priority"]:
        categories_to_check = ["TaskPriority", "Priority", "task_priority", "priority"]
    elif clean_cat in ["milestonestatus", "milestone_status"]:
        categories_to_check = ["MilestoneStatus", "ProjectStatus", "milestone_status"]
    elif clean_cat in ["issuestatus", "issue_status"]:
        categories_to_check = ["IssueStatus", "issue_status", "Status"]

    result = await db.execute(
        select(MasterLookup)
        .where(
            or_(
                func.lower(MasterLookup.category) == clean_cat,
                MasterLookup.category.in_(categories_to_check)
            ),
            MasterLookup.is_active == True
        )
        .order_by(MasterLookup.order_index, MasterLookup.id)
    )
    items = result.scalars().all()
    
    seen = set()
    unique_items = []
    for item in items:
        clean_label = item.label.strip().lower()
        if clean_label not in seen:
            # Convert to dict for JSON serialization
            unique_items.append({
                "id": item.id,
                "category": item.category,
                "value": item.value,
                "label": item.label,
                "color": item.color,
                "icon": item.icon,
                "order_index": item.order_index,
                "is_active": item.is_active
            })
            seen.add(clean_label)
            
    await cache.set(cache_key, unique_items, expire=3600)
    return unique_items

@router.get("/lookups/{category}/search", response_model=List[MasterLookupResponse])
async def search_master_lookups(category: str, q: str = Query(..., min_length=1), limit: int = 20, db: AsyncSession = Depends(get_async_db)):
    from app.models.master import MasterLookup
    from sqlalchemy import select
    result = await db.execute(
        select(MasterLookup)
        .where(MasterLookup.category == category, MasterLookup.is_active == True, MasterLookup.label.ilike(f"%{q}%"))
        .order_by(MasterLookup.order_index)
        .limit(limit)
    )
    return result.scalars().all()

@router.get("/user-statuses", response_model=List[MasterResponse])
async def read_user_statuses(db: AsyncSession = Depends(get_async_db)):
    return await master_service.get_user_statuses(db)

@router.get("/user-statuses/search", response_model=List[MasterResponse])
async def search_user_statuses(q: str = Query(..., min_length=1), limit: int = 20, db: AsyncSession = Depends(get_async_db)):
    return await master_service.search_user_statuses(db, q, limit)

@router.get("/statuses", response_model=List[MasterResponse])
async def read_statuses(db: AsyncSession = Depends(get_async_db)):
    return await master_service.get_statuses(db)

@router.get("/statuses/search", response_model=List[MasterResponse])
async def search_statuses(q: str = Query(..., min_length=1), limit: int = 20, db: AsyncSession = Depends(get_async_db)):
    return await master_service.search_statuses(db, q, limit)

@router.get("/priorities", response_model=List[MasterResponse])
async def read_priorities(db: AsyncSession = Depends(get_async_db)):
    return await master_service.get_priorities(db)

@router.get("/priorities/search", response_model=List[MasterResponse])
async def search_priorities(q: str = Query(..., min_length=1), limit: int = 20, db: AsyncSession = Depends(get_async_db)):
    return await master_service.search_priorities(db, q, limit)

@router.get("/roles", response_model=List[RoleResponse])
async def read_roles(db: AsyncSession = Depends(get_async_db)):
    from sqlalchemy import func, select
    from app.models.user import User
    from app.models.roles import Role as RoleModel

    user_count_sq = (
        select(User.role_id, func.count(User.id).label("cnt"))
        .where(User.role_id.isnot(None))
        .group_by(User.role_id)
        .subquery()
    )
    rows = (await db.execute(
        select(RoleModel, func.coalesce(user_count_sq.c.cnt, 0).label("users_count"))
        .outerjoin(user_count_sq, RoleModel.id == user_count_sq.c.role_id)
    )).all()

    seen = set()
    result = []
    for role, uc in rows:
        n = role.name.strip().lower()
        if n not in seen:
            seen.add(n)
            result.append({
                "id": role.id,
                "name": role.name,
                "description": role.description,
                "permissions": role.permissions or {},
                "users_count": uc,
            })
    return result

@router.get("/roles/search", response_model=List[RoleResponse])
async def search_roles(q: str = Query(..., min_length=1), limit: int = 20, db: AsyncSession = Depends(get_async_db)):
    from sqlalchemy import func, select
    from app.models.user import User
    from app.models.roles import Role as RoleModel

    user_count_sq = (
        select(User.role_id, func.count(User.id).label("cnt"))
        .where(User.role_id.isnot(None))
        .group_by(User.role_id)
        .subquery()
    )
    rows = (await db.execute(
        select(RoleModel, func.coalesce(user_count_sq.c.cnt, 0).label("users_count"))
        .outerjoin(user_count_sq, RoleModel.id == user_count_sq.c.role_id)
        .where(RoleModel.name.ilike(f"%{q}%"))
        .limit(limit)
    )).all()

    seen = set()
    result = []
    for role, uc in rows:
        n = role.name.strip().lower()
        if n not in seen:
            seen.add(n)
            result.append({
                "id": role.id,
                "name": role.name,
                "description": role.description,
                "permissions": role.permissions or {},
                "users_count": uc,
            })
    return result

@router.get("/roles/{role_id}", response_model=RoleWithUsersResponse)
async def read_role(role_id: int, db: AsyncSession = Depends(get_async_db)):
    db_role = await master_service.get_role(db, role_id)
    if db_role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return db_role

@router.post("/roles", response_model=RoleResponse)
async def create_role(role: RoleCreate, db: AsyncSession = Depends(get_async_db), current_user=Depends(allow_role_manage)):
    return await master_service.create_role(db, role.model_dump())

@router.put("/roles/{role_id}", response_model=RoleResponse)
async def update_role(role_id: int, role: RoleUpdate, db: AsyncSession = Depends(get_async_db), current_user=Depends(allow_role_manage)):
    return await master_service.update_role(db, role_id, role.model_dump(exclude_unset=True))

@router.delete("/roles/{role_id}")
async def delete_role(role_id: int, db: AsyncSession = Depends(get_async_db), current_user=Depends(allow_role_manage)):
    success = await master_service.delete_role(db, role_id)
    if not success:
        raise HTTPException(status_code=404, detail="Role not found")
    return {"message": "Role deleted successfully"}

@router.post("/roles/bulk-permissions")
async def update_bulk_role_permissions(update_data: BulkRolePermissionsUpdate, db: AsyncSession = Depends(get_async_db), current_user=Depends(allow_role_manage)):
    try:
        await master_service.update_bulk_role_permissions(db, update_data.role_permissions)
        return {"message": "Permissions updated successfully for all roles"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@router.get("/skills", response_model=List[SkillResponse])
async def read_skills(db: AsyncSession = Depends(get_async_db)):
    return await master_service.get_skills(db)

@router.get("/skills/search", response_model=List[SkillResponse])
async def search_skills(q: str = Query(..., min_length=1), limit: int = 20, db: AsyncSession = Depends(get_async_db)):
    return await master_service.search_skills(db, q, limit)
