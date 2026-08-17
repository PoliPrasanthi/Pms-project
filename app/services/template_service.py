from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.template import ProjectTemplate, TemplateTask
from app.schemas.template import ProjectTemplateCreate, ProjectTemplateUpdate


def _template_query():
    return (
        select(ProjectTemplate)
        .options(
            selectinload(ProjectTemplate.tasks),
            selectinload(ProjectTemplate.created_by),
        )
        .where(ProjectTemplate.is_deleted == False)
    )


async def get_templates(db: AsyncSession, skip: int = 0, limit: int = 100) -> dict:
    stmt = select(ProjectTemplate).where(ProjectTemplate.is_deleted == False)
    count_stmt = stmt.with_only_columns(func.count(ProjectTemplate.id)).order_by(None)
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = stmt.options(
        selectinload(ProjectTemplate.tasks),
        selectinload(ProjectTemplate.created_by),
    )

    result = await db.execute(stmt.order_by(ProjectTemplate.name).offset(skip).limit(limit))
    items = result.scalars().all()
    return {"total": total, "items": items}


async def get_template(db: AsyncSession, template_id: int) -> Optional[ProjectTemplate]:
    result = await db.execute(
        _template_query().where(ProjectTemplate.id == template_id)
    )
    return result.scalar_one_or_none()


async def create_template(
    db: AsyncSession,
    data: ProjectTemplateCreate,
    created_by_id: Optional[int] = None,
) -> ProjectTemplate:
    from app.utils.ids import generate_public_id

    existing = (await db.execute(
        select(ProjectTemplate).where(ProjectTemplate.name == data.name, ProjectTemplate.is_deleted == False)
    )).scalar_one_or_none()
    if existing:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"A template with name '{data.name}' already exists.")

    db_template = ProjectTemplate(
        public_id     = generate_public_id("TPL-"),
        name          = data.name,
        description   = data.description,
        billing_type  = data.billing_type,
        is_public     = data.is_public,
        created_by_id = created_by_id,
    )
    db.add(db_template)
    await db.flush()

    for i, t in enumerate(data.tasks):
        db.add(TemplateTask(
            template_id     = db_template.id,
            title           = t.title,
            description     = t.description,
            estimated_hours = t.estimated_hours,
            duration        = t.duration,
            billing_type    = t.billing_type,
            tags            = t.tags,
            order_index     = t.order_index if t.order_index else i,
        ))

    await db.commit()
    return await get_template(db, db_template.id)


async def update_template(
    db: AsyncSession,
    template_id: int,
    data: ProjectTemplateUpdate,
) -> Optional[ProjectTemplate]:
    template = (await db.execute(
        select(ProjectTemplate).where(ProjectTemplate.id == template_id)
    )).scalar_one_or_none()
    if not template:
        return None

    if data.name is not None and data.name != template.name:
        existing = (await db.execute(
            select(ProjectTemplate).where(
                ProjectTemplate.name == data.name,
                ProjectTemplate.id != template_id,
                ProjectTemplate.is_deleted == False
            )
        )).scalar_one_or_none()
        if existing:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail=f"A template with name '{data.name}' already exists.")
        template.name = data.name

    if data.description is not None:
        template.description = data.description
    if data.billing_type is not None:
        template.billing_type = data.billing_type
    if data.is_public is not None:
        template.is_public = data.is_public

    if data.tasks is not None:
        existing_tasks = (await db.execute(
            select(TemplateTask).where(TemplateTask.template_id == template_id)
        )).scalars().all()
        for t in existing_tasks:
            await db.delete(t)
        await db.flush()

        for i, t in enumerate(data.tasks):
            db.add(TemplateTask(
                template_id     = template_id,
                title           = t.title,
                description     = t.description,
                estimated_hours = t.estimated_hours,
                duration        = t.duration,
                billing_type    = t.billing_type,
                tags            = t.tags,
                order_index     = t.order_index if t.order_index else i,
            ))

    await db.commit()
    return await get_template(db, template_id)


async def add_template_task(
    db: AsyncSession,
    template_id: int,
    task_data: dict,
) -> Optional[ProjectTemplate]:
    template = (await db.execute(
        select(ProjectTemplate).where(ProjectTemplate.id == template_id)
    )).scalar_one_or_none()
    if not template:
        return None

    max_idx = (await db.execute(
        select(TemplateTask.order_index)
        .where(TemplateTask.template_id == template_id)
        .order_by(TemplateTask.order_index.desc())
        .limit(1)
    )).scalar() or 0

    db.add(TemplateTask(
        template_id     = template_id,
        title           = task_data["title"],
        description     = task_data.get("description"),
        estimated_hours = task_data.get("estimated_hours"),
        duration        = task_data.get("duration"),
        billing_type    = task_data.get("billing_type"),
        tags            = task_data.get("tags"),
        order_index     = max_idx + 1,
    ))
    await db.commit()
    return await get_template(db, template_id)


async def remove_template_task(
    db: AsyncSession,
    template_id: int,
    task_id: int,
) -> Optional[ProjectTemplate]:
    task = (await db.execute(
        select(TemplateTask).where(
            TemplateTask.id == task_id,
            TemplateTask.template_id == template_id,
        )
    )).scalar_one_or_none()
    if not task:
        return None
    await db.delete(task)
    await db.commit()
    return await get_template(db, template_id)


async def delete_template(db: AsyncSession, template_id: int) -> bool:
    template = (await db.execute(
        select(ProjectTemplate).where(ProjectTemplate.id == template_id)
    )).scalar_one_or_none()
    if not template:
        return False
    template.is_deleted = True
    await db.commit()
    return True
