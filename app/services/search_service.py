from __future__ import annotations

from typing import List, Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.task import Task
from app.models.issue import Issue


class SearchService:

    async def global_search(
        self,
        db: AsyncSession,
        query: str,
        limit: int = 15,
        current_user = None,
    ) -> List[dict]:
        if not query:
            return []

        q = f"%{query}%"
        search_results: List[dict] = []
        from app.core.security import get_user_view_level
        proj_view_level = get_user_view_level(current_user, 'proj-view') if current_user else 'O'
        task_view_level = get_user_view_level(current_user, 'task-view') if current_user else 'O'
        issue_view_level = get_user_view_level(current_user, 'issue-view') if current_user else 'O'

        from app.models.project import ProjectMember
        proj_stmt = select(Project).where(
            Project.is_deleted == 0, 
            or_(Project.project_name.ilike(q), Project.public_id.ilike(q))
        )
        if proj_view_level != 'All' and current_user:
            proj_stmt = proj_stmt.outerjoin(ProjectMember, ProjectMember.project_id == Project.id).where(
                or_(
                    Project.owner_id == current_user.id,
                    Project.project_manager_id == current_user.id,
                    Project.delivery_head_id == current_user.id,
                    ProjectMember.user_id == current_user.id
                )
            )
        
        from app.models.task import task_assignees, task_owners
        task_stmt = select(Task).where(
            Task.is_deleted == 0, 
            or_(Task.task_name.ilike(q), Task.public_id.ilike(q))
        )
        if task_view_level != 'All' and current_user:
            has_assignee = select(1).select_from(task_assignees).where(task_assignees.c.task_id == Task.id, task_assignees.c.user_id == current_user.id).correlate(Task)
            has_owner_t = select(1).select_from(task_owners).where(task_owners.c.task_id == Task.id, task_owners.c.user_id == current_user.id).correlate(Task)
            
            conditions = [
                Task.assignee_id == current_user.id, 
                has_assignee.exists(), 
                Task.owner_id == current_user.id, 
                has_owner_t.exists(),
                Task.created_by_id == current_user.id
            ]
            
            if task_view_level != 'O':
                is_member_of_projects_task = select(Project.id).outerjoin(ProjectMember, ProjectMember.project_id == Project.id).where(
                    or_(
                        Project.owner_id == current_user.id,
                        Project.project_manager_id == current_user.id,
                        Project.delivery_head_id == current_user.id,
                        ProjectMember.user_id == current_user.id
                    )
                )
                conditions.append(Task.project_id.in_(is_member_of_projects_task))
                
            task_stmt = task_stmt.where(or_(*conditions))
        
        from app.models.issue import issue_assignees
        issue_stmt = select(Issue).where(
            Issue.is_deleted == 0, 
            or_(Issue.bug_name.ilike(q), Issue.public_id.ilike(q))
        )
        if issue_view_level != 'All' and current_user:
            has_i_assignee = select(1).select_from(issue_assignees).where(issue_assignees.c.issue_id == Issue.id, issue_assignees.c.user_id == current_user.id).correlate(Issue)
            
            conditions = [
                Issue.assignee_id == current_user.id, 
                has_i_assignee.exists(), 
                Issue.reporter_id == current_user.id
            ]
            
            if issue_view_level != 'O':
                is_member_of_projects_issue = select(Project.id).outerjoin(ProjectMember, ProjectMember.project_id == Project.id).where(
                    or_(
                        Project.owner_id == current_user.id,
                        Project.project_manager_id == current_user.id,
                        Project.delivery_head_id == current_user.id,
                        ProjectMember.user_id == current_user.id
                    )
                )
                conditions.append(Issue.project_id.in_(is_member_of_projects_issue))
                
            issue_stmt = issue_stmt.where(or_(*conditions))

        projects = (await db.execute(proj_stmt.limit(limit))).scalars().unique().all()
        search_results.extend(
            {"type": "project", "id": p.public_id or f"PRJ-{p.id}", "title": p.project_name, "project_name": p.project_name, "path": f"/projects/{p.id}"}
            for p in projects
        )

        tasks = (await db.execute(task_stmt.limit(limit))).scalars().unique().all()
        search_results.extend(
            {
                "type": "task", 
                "id": t.public_id or f"TSK-{t.id}", 
                "title": t.task_name, 
                "path": f"/tasks/{t.id}",
                "estimated_hours": float(t.estimated_hours or 0.0),
                "work_hours": float(t.work_hours or 0.0)
            }
            for t in tasks
        )

        issues = (await db.execute(issue_stmt.limit(limit))).scalars().unique().all()
        search_results.extend(
            {
                "type": "issue", 
                "id": i.public_id or f"ISS-{i.id}", 
                "title": i.bug_name, 
                "path": f"/issues/{i.id}",
                "estimated_hours": float(i.estimated_hours or 0.0)
            }
            for i in issues
        )

        from app.models.milestone import Milestone
        milestones = (await db.execute(
            select(Milestone).where(
                Milestone.is_deleted == 0, 
                or_(
                    Milestone.milestone_name.ilike(q), 
                    Milestone.public_id.ilike(q)
                )
            ).limit(limit)
        )).scalars().all()
        search_results.extend(
            {"type": "milestone", "id": m.public_id, "title": m.milestone_name, "path": f"/milestones/{m.id}"}
            for m in milestones
        )

        from app.models.user import User
        users = (await db.execute(
            select(User).where(
                User.is_active == True,
                or_(
                    User.first_name.ilike(q), 
                    User.last_name.ilike(q),
                    User.email.ilike(q)
                )
            ).limit(limit)
        )).scalars().all()
        search_results.extend(
            {"type": "user", "id": f"USR-{u.id}", "title": f"{u.first_name} {u.last_name}".strip(), "email": u.email, "path": f"/users/{u.id}"}
            for u in users
        )

        return search_results

    async def search_work_items(
        self,
        db: AsyncSession,
        query: str = "",
        project_id: Optional[int] = None,
        limit: int = 20,
        current_user = None,
        assignee_me: bool = False,
        exclude_completed: bool = False,
    ) -> List[dict]:
        search_results: List[dict] = []
        from app.core.security import get_user_view_level
        from app.models.master import MasterLookup
        task_view_level = get_user_view_level(current_user, 'task-view') if current_user else 'O'
        issue_view_level = get_user_view_level(current_user, 'issue-view') if current_user else 'O'
        
        task_stmt = select(Task).where(Task.is_deleted == 0)
        
        if assignee_me and current_user:
            from app.models.task import task_assignees, task_owners
            has_assignee = select(1).select_from(task_assignees).where(task_assignees.c.task_id == Task.id, task_assignees.c.user_id == current_user.id).correlate(Task)
            has_owner = select(1).select_from(task_owners).where(task_owners.c.task_id == Task.id, task_owners.c.user_id == current_user.id).correlate(Task)
            task_stmt = task_stmt.where(
                or_(
                    Task.assignee_id == current_user.id, 
                    has_assignee.exists(),
                    Task.owner_id == current_user.id,
                    has_owner.exists()
                )
            )
        elif task_view_level != 'All' and current_user:
            from app.models.task import task_assignees, task_owners
            from app.models.project import Project, ProjectMember
            has_assignee = select(1).select_from(task_assignees).where(task_assignees.c.task_id == Task.id, task_assignees.c.user_id == current_user.id).correlate(Task)
            has_owner = select(1).select_from(task_owners).where(task_owners.c.task_id == Task.id, task_owners.c.user_id == current_user.id).correlate(Task)
            
            conditions = [
                Task.assignee_id == current_user.id, 
                has_assignee.exists(),
                Task.owner_id == current_user.id,
                has_owner.exists(),
                Task.created_by_id == current_user.id
            ]
            
            if task_view_level != 'O':
                is_member_of_projects_task = select(Project.id).outerjoin(ProjectMember, ProjectMember.project_id == Project.id).where(
                    or_(
                        Project.owner_id == current_user.id,
                        Project.project_manager_id == current_user.id,
                        Project.delivery_head_id == current_user.id,
                        ProjectMember.user_id == current_user.id
                    )
                )
                conditions.append(Task.project_id.in_(is_member_of_projects_task))
            
            task_stmt = task_stmt.where(or_(*conditions))
        
        issue_stmt = select(Issue).where(Issue.is_deleted == 0)
        
        if assignee_me and current_user:
            from app.models.issue import issue_assignees
            has_i_assignee = select(1).select_from(issue_assignees).where(issue_assignees.c.issue_id == Issue.id, issue_assignees.c.user_id == current_user.id).correlate(Issue)
            issue_stmt = issue_stmt.where(
                or_(
                    Issue.assignee_id == current_user.id, 
                    has_i_assignee.exists()
                )
            )
        elif issue_view_level != 'All' and current_user:
            from app.models.issue import issue_assignees
            from app.models.project import Project, ProjectMember
            has_i_assignee = select(1).select_from(issue_assignees).where(issue_assignees.c.issue_id == Issue.id, issue_assignees.c.user_id == current_user.id).correlate(Issue)
            
            conditions = [
                Issue.assignee_id == current_user.id, 
                has_i_assignee.exists(), 
                Issue.reporter_id == current_user.id
            ]
            
            if issue_view_level != 'O':
                is_member_of_projects_issue = select(Project.id).outerjoin(ProjectMember, ProjectMember.project_id == Project.id).where(
                    or_(
                        Project.owner_id == current_user.id,
                        Project.project_manager_id == current_user.id,
                        Project.delivery_head_id == current_user.id,
                        ProjectMember.user_id == current_user.id
                    )
                )
                conditions.append(Issue.project_id.in_(is_member_of_projects_issue))
            
            issue_stmt = issue_stmt.where(or_(*conditions))

        if query:
            q = f"%{query}%"
            task_stmt  = task_stmt.where(or_(Task.task_name.ilike(q),  Task.public_id.ilike(q)))
            issue_stmt = issue_stmt.where(or_(Issue.bug_name.ilike(q), Issue.public_id.ilike(q)))
        if project_id:
            task_stmt  = task_stmt.where(Task.project_id  == project_id)
            issue_stmt = issue_stmt.where(Issue.project_id == project_id)
            
        if exclude_completed:
            task_stmt = task_stmt.outerjoin(MasterLookup, Task.status_id == MasterLookup.id).where(
                or_(MasterLookup.value.is_(None), MasterLookup.value.notin_(['Completed', 'Closed']))
            )
            issue_stmt = issue_stmt.outerjoin(MasterLookup, Issue.status_id == MasterLookup.id).where(
                or_(MasterLookup.value.is_(None), MasterLookup.value.notin_(['Completed', 'Closed']))
            )

        tasks  = (await db.execute(task_stmt.limit(limit))).scalars().unique().all()
        issues = (await db.execute(issue_stmt.limit(limit))).scalars().unique().all()

        search_results.extend(
            {
                "type": "task",  
                "id": t.id, 
                "public_id": t.public_id or f"TSK-{t.id}", 
                "name": t.task_name, 
                "title": t.task_name,
                "estimated_hours": float(t.estimated_hours or 0.0),
                "work_hours": float(t.work_hours or 0.0)
            }
            for t in tasks
        )
        search_results.extend(
            {
                "type": "issue", 
                "id": i.id, 
                "public_id": i.public_id or f"ISS-{i.id}", 
                "name": i.bug_name, 
                "title": i.bug_name,
                "estimated_hours": float(i.estimated_hours or 0.0)
            }
            for i in issues
        )
        return search_results


search_service = SearchService()
