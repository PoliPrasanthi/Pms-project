from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.masters import UserStatus, Skill, Status, Priority
from app.models.roles import Role
from app.models.user import User
from app.core.cache import cached, invalidate_cache


@cached(ttl=3600, key_prefix="masters:user_statuses")
async def get_user_statuses(db: AsyncSession) -> List[UserStatus]:
    items = (await db.execute(select(UserStatus))).scalars().all()
    seen = set()
    unique = []
    for item in items:
        n = item.name.strip().lower()
        if n not in seen:
            unique.append(item)
            seen.add(n)
    return unique

@cached(ttl=3600, key_prefix="masters:statuses")
async def get_statuses(db: AsyncSession) -> List[Status]:
    items = (await db.execute(select(Status))).scalars().all()
    seen = set()
    unique = []
    for item in items:
        n = item.name.strip().lower()
        if n not in seen:
            unique.append(item)
            seen.add(n)
    return unique

@cached(ttl=3600, key_prefix="masters:priorities")
async def get_priorities(db: AsyncSession) -> List[Priority]:
    items = (await db.execute(select(Priority))).scalars().all()
    seen = set()
    unique = []
    for item in items:
        n = item.name.strip().lower()
        if n not in seen:
            unique.append(item)
            seen.add(n)
    return unique

@cached(ttl=3600, key_prefix="masters:skills")
async def get_skills(db: AsyncSession) -> List[Skill]:
    items = (await db.execute(select(Skill))).scalars().all()
    seen = set()
    unique = []
    for item in items:
        n = item.name.strip().lower()
        if n not in seen:
            unique.append(item)
            seen.add(n)
    return unique

async def get_roles(db: AsyncSession) -> List[Role]:
    items = (await db.execute(select(Role).options(selectinload(Role.users)))).scalars().all()
    seen = set()
    unique = []
    for item in items:
        n = item.name.strip().lower()
        if n not in seen:
            unique.append(item)
            seen.add(n)
    return unique

async def get_role(db: AsyncSession, role_id: int) -> Optional[Role]:
    result = await db.execute(
        select(Role).options(
            selectinload(Role.users).selectinload(User.status),
            selectinload(Role.users).selectinload(User.role),
            selectinload(Role.users).selectinload(User.manager),
            selectinload(Role.users).selectinload(User.skills)
        ).where(Role.id == role_id)
    )
    return result.scalar_one_or_none()

async def create_role(db: AsyncSession, role: dict) -> Role:
    from fastapi import HTTPException
    user_ids = role.pop("user_ids", [])
    existing = (await db.execute(select(Role).where(Role.name == role["name"]))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail=f"A role named '{role['name']}' already exists.")
    db_role = Role(**role)
    db.add(db_role)
    await db.flush()

    if user_ids:
        await db.execute(sa_update(User).where(User.id.in_(user_ids)).values(role_id=db_role.id))

    await db.commit()
    await db.refresh(db_role)
    await invalidate_cache("masters:roles")
    return db_role

async def update_role(db: AsyncSession, role_id: int, role: dict) -> Optional[Role]:
    result = await db.execute(select(Role).where(Role.id == role_id))
    db_role = result.scalar_one_or_none()
    if not db_role:
        return None

    user_ids = role.pop("user_ids", None)
    for key, value in role.items():
        setattr(db_role, key, value)

    if user_ids is not None:
        await db.execute(sa_update(User).where(User.role_id == role_id).values(role_id=None))
        if user_ids:
            await db.execute(sa_update(User).where(User.id.in_(user_ids)).values(role_id=db_role.id))

    await db.commit()
    await db.refresh(db_role)
    await invalidate_cache("masters:roles")
    return db_role

async def delete_role(db: AsyncSession, role_id: int) -> bool:
    result = await db.execute(select(Role).where(Role.id == role_id))
    db_role = result.scalar_one_or_none()
    if not db_role:
        return False
    await db.delete(db_role)
    await db.commit()
    await invalidate_cache("masters:roles")
    return True

async def search_statuses(db: AsyncSession, query: str, limit: int = 20) -> List[Status]:
    if not query:
        return []
    return (await db.execute(select(Status).where(Status.name.ilike(f"%{query}%")).limit(limit))).scalars().all()

async def search_priorities(db: AsyncSession, query: str, limit: int = 20) -> List[Priority]:
    if not query:
        return []
    return (await db.execute(select(Priority).where(Priority.name.ilike(f"%{query}%")).limit(limit))).scalars().all()

async def search_user_statuses(db: AsyncSession, query: str, limit: int = 20) -> List[UserStatus]:
    if not query:
        return []
    return (await db.execute(select(UserStatus).where(UserStatus.name.ilike(f"%{query}%")).limit(limit))).scalars().all()

async def search_roles(db: AsyncSession, query: str, limit: int = 20) -> List[Role]:
    if not query:
        return []
    return (await db.execute(select(Role).where(Role.name.ilike(f"%{query}%")).limit(limit))).scalars().all()

async def search_skills(db: AsyncSession, query: str, limit: int = 20) -> List[Skill]:
    if not query:
        return []
    return (await db.execute(select(Skill).where(Skill.name.ilike(f"%{query}%")).limit(limit))).scalars().all()

async def update_bulk_role_permissions(db: AsyncSession, role_permissions: dict):
    for role_id_str, perms in role_permissions.items():
        role_id = int(role_id_str)
        await db.execute(sa_update(Role).where(Role.id == role_id).values(permissions=perms))
    await db.commit()
