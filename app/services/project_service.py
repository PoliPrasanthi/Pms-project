from __future__ import annotations

from typing import List, Optional

from sqlalchemy import func, or_, select, case, desc, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
import json

from app.models.project import Project, ProjectMember
from app.models.user import User
from app.models.task import Task
from app.models.issue import Issue
from app.models.milestone import Milestone
from app.models.document import Document
from app.models.template import ProjectTemplate, TemplateTask
from app.schemas.project import ProjectCreate, ProjectUpdate, ProjectSyncUpdate
from app.utils.ids import generate_public_id, get_next_project_id
from app.utils.audit_utils import capture_audit_details, write_audit


def _project_query_options():
    return [
        selectinload(Project.owner).selectinload(User.role),
        selectinload(Project.project_manager).selectinload(User.role),
        selectinload(Project.delivery_head).selectinload(User.role),
        selectinload(Project.source_template),
        selectinload(Project.team_members).selectinload(ProjectMember.user).selectinload(User.role),
        selectinload(Project.status_master),
        selectinload(Project.priority_master),
        selectinload(Project.documents).selectinload(Document.uploaded_by),
        selectinload(Project.stats),
    ]

def _project_query(extra_options=()):
    return select(Project).options(*_project_query_options(), *extra_options)


async def _batch_enrich_projects(db: AsyncSession, projects: List[Project]) -> None:
    if not projects:
        return
        
    from sqlalchemy import select, func, case
    from app.models.task import Task
    from app.models.issue import Issue
    from app.models.milestone import Milestone
    from app.models.master import MasterLookup
    
    proj_ids = [p.id for p in projects if getattr(p, "id", None)]
    if not proj_ids:
        return
        
    task_stats_stmt = select(
        Task.project_id,
        func.count(Task.id).label("total_tasks"),
        func.sum(case((MasterLookup.label.in_(["Completed", "Closed", "Done", "Finished"]), 1), else_=0)).label("completed_tasks")
    ).outerjoin(MasterLookup, Task.status_id == MasterLookup.id).where(
        Task.project_id.in_(proj_ids),
        Task.is_deleted == False
    ).group_by(Task.project_id)
    
    issue_stats_stmt = select(
        Issue.project_id,
        func.count(Issue.id).label("total_issues")
    ).where(
        Issue.project_id.in_(proj_ids),
        Issue.is_deleted == False
    ).group_by(Issue.project_id)
    
    ms_stats_stmt = select(
        Milestone.project_id,
        func.count(Milestone.id).label("total_ms")
    ).where(
        Milestone.project_id.in_(proj_ids),
        Milestone.is_deleted == False
    ).group_by(Milestone.project_id)
    
    task_rows = (await db.execute(task_stats_stmt)).all()
    issue_rows = (await db.execute(issue_stats_stmt)).all()
    ms_rows = (await db.execute(ms_stats_stmt)).all()
    
    task_counts = {row.project_id: int(row.total_tasks or 0) for row in task_rows}
    completed_counts = {row.project_id: int(row.completed_tasks or 0) for row in task_rows}
    issue_counts = {row.project_id: int(row.total_issues or 0) for row in issue_rows}
    ms_counts = {row.project_id: int(row.total_ms or 0) for row in ms_rows}
    
    for p in projects:
        t_count = task_counts.get(p.id, 0)
        c_count = completed_counts.get(p.id, 0)
        i_count = issue_counts.get(p.id, 0)
        m_count = ms_counts.get(p.id, 0)
        
        p._dynamic_task_count = t_count
        p._dynamic_completed_task_count = c_count
        p._dynamic_issue_count = i_count
        p._dynamic_milestone_count = m_count
        
        if t_count > 0:
            p._dynamic_completion_percentage = int(round((c_count / t_count) * 100))
        else:
            p._dynamic_completion_percentage = 0


async def _enrich_project(db: AsyncSession, project: Project) -> Project:
    await _batch_enrich_projects(db, [project])
    return project


async def get_project(db: AsyncSession, project_id: int) -> Optional[Project]:
    result = await db.execute(
        _project_query().where(Project.id == project_id, Project.is_deleted == False)
    )
    project = result.scalar_one_or_none()
    if project:
        await _enrich_project(db, project)
    return project


async def get_projects(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
    status_ids: Optional[List[int]] = None,
    priority_ids: Optional[List[int]] = None,
    manager_emails: Optional[List[str]] = None,
    member_email: Optional[str] = None,
    is_archived: Optional[bool] = None,
    is_template: Optional[bool] = None,
    include_all: bool = False,
    search: Optional[str] = None,
    sort_by: Optional[str] = None,
    current_user=None,
    view_level: Optional[str] = None,
    **kwargs
) -> dict:
    # Determine user_id and effective view level
    user_id = getattr(current_user, "id", None) if current_user else None
    eff_view_level = view_level if current_user else None
    
    eff_archived = None if include_all else is_archived
    eff_template = None if include_all else is_template

    stmt = _project_query()
    from app.models.master import MasterLookup
    count_stmt = select(
        func.count(Project.id).label("total"),
        func.sum(case((MasterLookup.label.in_(["Completed", "Closed", "Done", "Finished"]), 1), else_=0)).label("completed"),
        func.sum(case((MasterLookup.label.in_(["Planning", "On Hold", "Draft"]), 1), else_=0)).label("planning")
    ).outerjoin(MasterLookup, Project.status_id == MasterLookup.id).where(Project.is_deleted == False)
    
    filters = [Project.is_deleted == False]
    
    if eff_archived is not None:
        filters.append(Project.is_archived == eff_archived)
    if eff_template is not None:
        filters.append(Project.is_template == eff_template)
        
    if status_ids:
        filters.append(Project.status_id.in_(status_ids))
    if priority_ids:
        filters.append(Project.priority_id.in_(priority_ids))
        
    if manager_emails:
        from app.models.user import User
        manager_subq = select(User.id).where(User.email.in_(manager_emails))
        filters.append(Project.project_manager_id.in_(manager_subq))
        
    if member_email:
        from app.models.user import User
        filters.append(Project.team_members.any(ProjectMember.user.has(User.email == member_email)))
        
    if search:
        q = f"%{search}%"
        filters.append(
            or_(
                Project.project_name.ilike(q),
                Project.public_id.ilike(q),
                Project.customer_name.ilike(q),
                Project.client_name.ilike(q)
            )
        )
        
    if user_id and eff_view_level:
        from app.core.security import normalize_view_level
        norm_level = normalize_view_level(eff_view_level)
        if norm_level == "O":
            filters.append(
                or_(
                    Project.owner_id == user_id,
                    Project.project_manager_id == user_id,
                    Project.team_members.any(ProjectMember.user_id == user_id)
                )
            )
        elif norm_level == "A":
            proj_id_stmt = select(Project.id).where(
                Project.is_deleted == False,
                or_(
                    Project.owner_id == user_id,
                    Project.project_manager_id == user_id,
                    Project.delivery_head_id == user_id,
                    Project.team_members.any(ProjectMember.user_id == user_id)
                )
            )
            allowed_project_ids = (await db.execute(proj_id_stmt)).scalars().all()
            if not allowed_project_ids:
                return {"total": 0, "status_counts": {"active": 0, "completed": 0, "planning": 0}, "items": []}
            filters.append(Project.id.in_(allowed_project_ids))

    stmt = stmt.where(*filters)
    count_stmt = count_stmt.where(*filters)

    stats_row = (await db.execute(count_stmt)).first()
    total = stats_row.total if stats_row and stats_row.total else 0
    final_stats = {
        "active": total - int(stats_row.completed or 0) - int(stats_row.planning or 0) if stats_row else 0,
        "completed": int(stats_row.completed) if stats_row and stats_row.completed else 0,
        "planning": int(stats_row.planning) if stats_row and stats_row.planning else 0,
    }

    if total == 0:
        return {"total": 0, "status_counts": final_stats, "items": []}

    order_by_clause = desc(Project.id)
    if sort_by:
        from sqlalchemy import asc
        parts = sort_by.split(",")
        orders = []
        for part in parts:
            part = part.strip()
            if not part: continue
            is_desc = part.startswith("-")
            field_name = part.lstrip("-")
            direction = desc if is_desc else asc
            
            if field_name == "project_name":
                orders.append(direction(Project.project_name))
            elif field_name == "expected_start_date":
                orders.append(direction(Project.expected_start_date))
            elif field_name == "expected_end_date":
                orders.append(direction(Project.expected_end_date))
            elif field_name == "status":
                orders.append(direction(Project.status_id))
            elif field_name == "priority":
                orders.append(direction(Project.priority_id))
            elif field_name == "public_id":
                orders.append(direction(Project.public_id))
            elif field_name == "completion_percentage":
                orders.append(direction(Project.completion_percentage))
        
        if orders:
            order_by_clause = orders
        else:
            order_by_clause = [order_by_clause]
    else:
        order_by_clause = [order_by_clause]

    result = await db.execute(stmt.order_by(*order_by_clause).offset(skip).limit(limit))
    projects = list(result.scalars().unique().all())
    await _batch_enrich_projects(db, projects)
    
    if kwargs.get('return_raw'):
        return {"total": total, "status_counts": final_stats, "items": projects}

    from app.schemas.project import ProjectResponse
    items = []
    
    for p in projects:
        item = ProjectResponse.model_validate(p).model_dump()
        items.append(item)

    return {"total": total, "status_counts": final_stats, "items": items}


async def search_projects(
    db: AsyncSession,
    query: str,
    limit: int = 20,
    current_user=None,
    view_level: str = 'O'
) -> List[Project]:
    if not query:
        return []
    q = f"%{query}%"
    stmt = _project_query().where(
        Project.is_deleted == False,
        or_(
            Project.project_name.ilike(q),
            Project.public_id.ilike(q),
            Project.project_id_sync.ilike(q),
            Project.customer_name.ilike(q),
        ),
    )
    
    if current_user and view_level != 'All':
        from app.models.project import ProjectMember
        if view_level == 'O':
            stmt = stmt.where(or_(Project.owner_id == current_user.id, Project.project_manager_id == current_user.id))
        elif view_level == 'A':
            stmt = stmt.outerjoin(ProjectMember, ProjectMember.project_id == Project.id).where(
                or_(
                    Project.owner_id == current_user.id,
                    Project.project_manager_id == current_user.id,
                    Project.delivery_head_id == current_user.id,
                    ProjectMember.user_id == current_user.id
                )
            )

    result = await db.execute(stmt.limit(limit))
    projects = list(result.scalars().unique().all())
    await _batch_enrich_projects(db, projects)
    return projects


async def create_project(
    db: AsyncSession,
    project: ProjectCreate,
    actor_id: str,
) -> Project:
    public_id = await get_next_project_id(db, Project)

    pm_id = project.project_manager_id
    if project.project_manager_email:
        pm_user = (await db.execute(select(User).where(User.email == project.project_manager_email))).scalar_one_or_none()
        if pm_user:
            pm_id = pm_user.id

    dh_id = project.delivery_head_id
    if project.delivery_head_email:
        dh_user = (await db.execute(select(User).where(User.email == project.delivery_head_email))).scalar_one_or_none()
        if dh_user:
            dh_id = dh_user.id

    db_project = Project(
        public_id               = public_id,
        project_name            = project.project_name,
        account_name            = project.account_name,
        customer_name           = project.customer_name,
        client_name             = project.client_name,
        project_id_sync         = project.project_id_sync,
        description             = project.description,
        tags                    = project.tags,
        billing_model           = project.billing_model,
        project_type            = project.project_type,
        project_status_external = project.project_status_external,
        project_manager_id      = pm_id,
        delivery_head_id        = dh_id,
        owner_id                = project.owner_id,
        template_id             = project.template_id,
        status_id               = project.status_id,
        priority_id             = project.priority_id,
        expected_start_date     = project.expected_start_date,
        expected_end_date       = project.expected_end_date,
        estimated_hours         = project.estimated_hours or 0.0,
        actual_start_date       = project.actual_start_date,
        actual_end_date         = project.actual_end_date,
        actual_hours            = project.actual_hours or 0.0,
        is_archived             = project.is_archived,
        is_template             = project.is_template,
        is_group                = project.is_group,
    )

    db.add(db_project)
    await db.flush()

    if project.template_id:
        await clone_from_template(db, db_project.id, project.template_id)

    members_to_add = {}

    if project.user_emails:
        users_result = await db.execute(select(User).where(User.email.in_(project.user_emails)))
        for u in users_result.scalars().all():
            members_to_add[u.id] = {"project_profile": "Member", "portal_profile": "User"}

    if project.owner_id:
        members_to_add[project.owner_id] = {"project_profile": "Project Lead", "portal_profile": "Administrator"}

    if pm_id:
        members_to_add[pm_id] = {"project_profile": "Project Manager", "portal_profile": "Administrator"}

    if dh_id:
        members_to_add[dh_id] = {"project_profile": "Delivery Head", "portal_profile": "Administrator"}

    for uid, profs in members_to_add.items():
        db.add(ProjectMember(
            project_id=db_project.id,
            user_id=uid,
            project_profile=profs["project_profile"],
            portal_profile=profs["portal_profile"],
        ))

    await write_audit(
        db, actor_id, "CREATE", "projects", db_project.id, db_project.id,
        [{"field_name": "project_name", "old_value": None, "new_value": project.project_name}],
    )
    await db.commit()
    return await get_project(db, db_project.id)


async def update_project(
    db: AsyncSession,
    project_id: int,
    project_update: ProjectUpdate,
    actor_id: Optional[str] = None,
) -> Optional[Project]:
    result = await db.execute(select(Project).where(Project.id == project_id))
    db_project = result.scalar_one_or_none()
    if not db_project:
        return None

    update_data = project_update.model_dump(
        exclude_unset=True,
        exclude={"user_emails", "project_manager_email", "delivery_head_email"},
    )

    if project_update.project_manager_email:
        pm_user = (await db.execute(
            select(User).where(User.email == project_update.project_manager_email)
        )).scalar_one_or_none()
        if pm_user:
            update_data["project_manager_id"] = pm_user.id

    if project_update.delivery_head_email:
        dh_user = (await db.execute(
            select(User).where(User.email == project_update.delivery_head_email)
        )).scalar_one_or_none()
        if dh_user:
            update_data["delivery_head_id"] = dh_user.id

    if "status_id" in update_data and update_data["status_id"] != db_project.status_id:
        update_data["previous_status_id"] = db_project.status_id
        update_data["is_processed"] = False

    if "priority_id" in update_data and update_data["priority_id"] != db_project.priority_id:
        update_data["is_processed"] = False

    changes = capture_audit_details(db_project, update_data)

    for key, value in update_data.items():
        setattr(db_project, key, value)

    if project_update.user_emails is not None:
        existing_members = (await db.execute(
            select(ProjectMember).where(ProjectMember.project_id == project_id)
        )).scalars().all()
        existing_user_ids = {m.user_id for m in existing_members}

        new_users = (await db.execute(
            select(User).where(User.email.in_(project_update.user_emails))
        )).scalars().all()
        new_user_ids = {u.id for u in new_users}

        for u in new_users:
            if u.id not in existing_user_ids:
                db.add(ProjectMember(
                    project_id      = project_id,
                    user_id         = u.id,
                    project_profile = "Member",
                    portal_profile  = "User",
                    is_processed    = False,
                ))

        exempt_uids = {db_project.owner_id, db_project.project_manager_id, db_project.delivery_head_id}
        for m in existing_members:
            if m.user_id not in new_user_ids and m.user_id not in exempt_uids:
                await db.delete(m)

    roles_to_ensure = []
    if db_project.owner_id:
        roles_to_ensure.append({"uid": db_project.owner_id, "profile": "Project Lead", "portal": "Administrator", "is_owner": True})
    if db_project.project_manager_id:
        roles_to_ensure.append({"uid": db_project.project_manager_id, "profile": "Project Manager", "portal": "Administrator", "is_owner": False})
    if db_project.delivery_head_id:
        roles_to_ensure.append({"uid": db_project.delivery_head_id, "profile": "Delivery Head", "portal": "Administrator", "is_owner": False})

    if roles_to_ensure:
        role_uids = [r["uid"] for r in roles_to_ensure]
        existing_roles_qs = (await db.execute(
            select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id.in_(role_uids))
        )).scalars().all()
        existing_by_uid = {m.user_id: m for m in existing_roles_qs}

        for r in roles_to_ensure:
            existing = existing_by_uid.get(r["uid"])
            if not existing:
                db.add(ProjectMember(
                    project_id      = project_id,
                    user_id         = r["uid"],
                    project_profile = r["profile"],
                    portal_profile  = r["portal"],
                    is_owner        = r["is_owner"],
                    is_processed    = False
                ))
            else:
                if r["is_owner"]:
                    existing.is_owner = True
                if existing.project_profile == "Member" and r["profile"] != "Member":
                    existing.project_profile = r["profile"]
                    existing.portal_profile = r["portal"]

    await write_audit(db, actor_id, "UPDATE", "projects", project_id, project_id, changes)
    await db.commit()
    return await get_project(db, project_id)


async def delete_project(
    db: AsyncSession,
    project_id: int,
    actor_id: Optional[str] = None,
) -> bool:
    from sqlalchemy import update
    from app.models.task import Task
    from app.models.issue import Issue
    from app.models.milestone import Milestone
    from app.models.task_list import TaskList

    result = await db.execute(select(Project).where(Project.id == project_id))
    db_project = result.scalar_one_or_none()
    if not db_project:
        return False

    await write_audit(
        db, actor_id, "DELETE", "projects", project_id, project_id,
        [{"field_name": "project_name", "old_value": db_project.project_name, "new_value": None}],
    )

    db_project.is_deleted = True
    db_project.is_active  = False

    await db.execute(update(Task).where(Task.project_id == project_id).values(is_deleted=True, is_active=False))
    await db.execute(update(Issue).where(Issue.project_id == project_id).values(is_deleted=True, is_active=False))
    await db.execute(update(Milestone).where(Milestone.project_id == project_id).values(is_deleted=True, is_active=False))
    await db.execute(update(TaskList).where(TaskList.project_id == project_id).values(is_deleted=True, is_active=False))

    await db.commit()
    return True


async def archive_project(
    db: AsyncSession,
    project_id: int,
    archived: bool = True,
    actor_id: Optional[str] = None,
) -> Optional[Project]:
    result = await db.execute(select(Project).where(Project.id == project_id))
    db_project = result.scalar_one_or_none()
    if not db_project:
        return None

    db_project.is_archived = archived
    await write_audit(
        db, actor_id or "system", "UPDATE", "projects", project_id, project_id,
        [{"field_name": "is_archived", "old_value": str(not archived), "new_value": str(archived)}],
    )
    await db.commit()
    return await get_project(db, project_id)


async def sync_project_fields(
    db: AsyncSession,
    project_id: int,
    sync_data: ProjectSyncUpdate,
    actor_id: Optional[str] = None,
) -> Optional[Project]:
    result = await db.execute(select(Project).where(Project.id == project_id))
    db_project = result.scalar_one_or_none()
    if not db_project:
        return None

    update_data = sync_data.model_dump(exclude_unset=True, exclude_none=True)
    changes = capture_audit_details(db_project, update_data)
    for key, value in update_data.items():
        setattr(db_project, key, value)

    await write_audit(db, actor_id or "sync", "UPDATE", "projects", project_id, project_id, changes)
    await db.commit()
    return await get_project(db, project_id)


async def add_project_member(
    db: AsyncSession,
    project_id: int,
    user_id: int,
    project_profile: str = "Member",
    portal_profile: str = "User",
) -> Optional[ProjectMember]:
    existing = (await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )).scalar_one_or_none()
    if existing:
        return existing

    member = ProjectMember(
        project_id      = project_id,
        user_id         = user_id,
        project_profile = project_profile,
        portal_profile  = portal_profile,
    )
    db.add(member)
    await db.commit()
    await db.refresh(member)
    return member


async def update_project_member(
    db: AsyncSession,
    project_id: int,
    user_id: int,
    profile_data: dict,
) -> Optional[ProjectMember]:
    member = (await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )).scalar_one_or_none()
    if not member:
        return None

    if "invitation_status_id" in profile_data and profile_data["invitation_status_id"] != member.invitation_status_id:
        member.previous_invitation_status_id = member.invitation_status_id
        member.is_processed = False

    for key, value in profile_data.items():
        if hasattr(member, key):
            setattr(member, key, value)

    await db.commit()
    await db.refresh(member)
    return member


async def remove_project_member(
    db: AsyncSession,
    project_id: int,
    user_id: int,
    owner_id: Optional[int] = None,
) -> bool:
    if owner_id and user_id == owner_id:
        return False
    result = (await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )).scalar_one_or_none()
    if not result:
        return False
    await db.delete(result)
    await db.commit()
    return True


async def clone_from_template(
    db: AsyncSession,
    project_id: int,
    template_id: int,
) -> None:
    from app.utils.ids import get_next_sequence_id
    from app.services.task_list_service import get_or_create_general_list

    project = (await db.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()
    project_name = project.project_name if project else ""

    general_list = await get_or_create_general_list(db, project_id)

    tmpl_tasks_result = await db.execute(
        select(TemplateTask)
        .where(TemplateTask.template_id == template_id)
        .order_by(TemplateTask.order_index)
    )

    tasks_to_add = tmpl_tasks_result.scalars().all()
    for tt in tasks_to_add:
        pid = await get_next_sequence_id(db, Task, project_name, project_id, "TSK")
        db.add(Task(
            public_id       = pid,
            task_name       = tt.title,
            description     = tt.description,
            project_id      = project_id,
            task_list_id    = general_list.id,
            estimated_hours = tt.estimated_hours,
            duration        = tt.duration,
            billing_type    = tt.billing_type or "Billable",
            tags            = tt.tags,
        ))
        await db.flush()
    await db.commit()
