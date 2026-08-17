from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException

from app.models.project import Project
from app.models.task import Task
from app.models.template import ProjectTemplate, TemplateTask
from app.schemas.template import TemplateCloneRequest


class TemplateCloningService:
    @staticmethod
    async def clone_project_to_template(
        db: AsyncSession,
        project_id: int,
        request: TemplateCloneRequest,
        user_id: int,
    ) -> ProjectTemplate:
        project = (await db.execute(
            select(Project).where(Project.id == project_id, Project.is_deleted == False)
        )).scalar_one_or_none()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        new_template = ProjectTemplate(
            name           = request.template_name,
            description    = project.description,
            billing_type   = project.billing_model,
            is_public      = True,
            created_by_id  = user_id,
        )
        db.add(new_template)
        await db.flush()

        task_stmt = select(Task).where(Task.project_id == project_id, Task.is_deleted == False)
        if not request.include_milestones:
            task_stmt = task_stmt.where(Task.milestone_id == None)
        task_stmt = task_stmt.order_by(Task.id)

        tasks = (await db.execute(task_stmt)).scalars().all()

        for order_idx, task in enumerate(tasks):
            db.add(TemplateTask(
                template_id     = new_template.id,
                title           = task.task_name,
                description     = task.description,
                estimated_hours = task.estimated_hours,
                duration        = task.duration,
                billing_type    = task.billing_type,
                tags            = task.tags,
                order_index     = order_idx,
            ))

        await db.flush()
        await db.commit()
        return new_template
