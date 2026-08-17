from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project_group import ProjectGroup
from app.schemas.project_group import ProjectGroupCreate, ProjectGroupUpdate
from app.utils.audit_utils import write_audit, capture_audit_details


async def get_project_group(db: AsyncSession, group_id: int) -> Optional[ProjectGroup]:
    return (await db.execute(select(ProjectGroup).where(ProjectGroup.id == group_id))).scalar_one_or_none()


async def get_project_groups(db: AsyncSession, skip: int = 0, limit: int = 100) -> dict:
    stmt = select(ProjectGroup)
    count_stmt = select(func.count(ProjectGroup.id))
    total = (await db.execute(count_stmt)).scalar() or 0
    items = (await db.execute(stmt.offset(skip).limit(limit))).scalars().all()
    return {"total": total, "items": items}


async def create_project_group(
    db: AsyncSession,
    group: ProjectGroupCreate,
    actor_id: Optional[str] = None,
) -> ProjectGroup:
    db_group = ProjectGroup(name=group.name, description=group.description)
    db.add(db_group)
    await db.flush()

    await write_audit(db, actor_id, "CREATE", "project_groups",
                resource_id=db_group.id, record_id=db_group.id,
                details=[{"field_name": "name", "old_value": None, "new_value": group.name}])

    await db.commit()
    await db.refresh(db_group)
    return db_group


async def update_project_group(
    db: AsyncSession,
    group_id: int,
    group_update: ProjectGroupUpdate,
    actor_id: Optional[str] = None,
) -> Optional[ProjectGroup]:
    db_group = await get_project_group(db, group_id)
    if not db_group:
        return None

    update_data = group_update.model_dump(exclude_unset=True)
    changes = capture_audit_details(db_group, update_data)

    for key, value in update_data.items():
        setattr(db_group, key, value)

    await write_audit(db, actor_id, "UPDATE", "project_groups",
                resource_id=group_id, record_id=group_id, details=changes)

    await db.commit()
    await db.refresh(db_group)
    return db_group


async def delete_project_group(
    db: AsyncSession,
    group_id: int,
    actor_id: Optional[str] = None,
) -> bool:
    db_group = await get_project_group(db, group_id)
    if not db_group:
        return False

    await write_audit(db, actor_id, "DELETE", "project_groups",
                resource_id=group_id, record_id=group_id,
                details=[{"field_name": "name", "old_value": db_group.name, "new_value": None}])

    await db.delete(db_group)
    await db.commit()
    return True


async def search_project_groups(db: AsyncSession, query: str, limit: int = 20) -> List[ProjectGroup]:
    if not query:
        return []
    q = f"%{query}%"
    return (await db.execute(select(ProjectGroup).where(ProjectGroup.name.ilike(q)).limit(limit))).scalars().all()
