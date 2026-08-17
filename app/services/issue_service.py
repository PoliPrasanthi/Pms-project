from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import func, or_, select, text, case
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
import json

from app.models.issue import Issue
from app.models.document import Document
from app.models.user import User
from app.schemas.issue import IssueCreate, IssueUpdate
from app.utils.ids import generate_public_id, get_next_sequence_id
from app.models.project import Project
from app.utils.audit_utils import capture_audit_details, write_audit


def _issue_query():
    from app.models.master import MasterLookup
    from app.models.milestone import Milestone
    return (
        select(Issue)
        .where(Issue.is_deleted == False)
        .options(
            selectinload(Issue.project),
            selectinload(Issue.milestone).selectinload(Milestone._owner_rel),
            selectinload(Issue.milestone).selectinload(Milestone.stats),
            selectinload(Issue.milestone).selectinload(Milestone.status_master),
            selectinload(Issue.milestone).selectinload(Milestone.priority_master),
            selectinload(Issue.milestone).selectinload(Milestone.project).selectinload(Project.owner),
            selectinload(Issue.milestone).selectinload(Milestone.project).selectinload(Project.project_manager),
            selectinload(Issue.milestone).selectinload(Milestone.project).selectinload(Project.delivery_head),
            selectinload(Issue.associated_team),
            selectinload(Issue.reporter),
            selectinload(Issue.assignee),
            selectinload(Issue.assignees),
            selectinload(Issue.followers),
            selectinload(Issue.documents),
            selectinload(Issue.status_master),
            selectinload(Issue.priority_master),
            selectinload(Issue.severity_master),
            selectinload(Issue.classification_master),
        )
    )


async def get_issue(db: AsyncSession, issue_id: int) -> Optional[Issue]:
    result = await db.execute(_issue_query().where(Issue.id == issue_id))
    return result.scalar_one_or_none()


async def get_active_issue_assignees(
    db: AsyncSession,
    current_user=None,
    view_level: str = 'O'
) -> List[User]:
    from app.models.issue import issue_assignees, issue_followers
    
    issue_filter = [Issue.is_deleted == False]
    
    if current_user and view_level != 'All':
        has_assignee_me = select(1).select_from(issue_assignees).where(issue_assignees.c.issue_id == Issue.id, issue_assignees.c.user_id == current_user.id).correlate(Issue)
        has_follower_me = select(1).select_from(issue_followers).where(issue_followers.c.issue_id == Issue.id, issue_followers.c.user_id == current_user.id).correlate(Issue)
        
        security_conds = [
            Issue.assignee_id == current_user.id,
            Issue.reporter_id == current_user.id,
            has_assignee_me.exists(),
            has_follower_me.exists()
        ]

        if view_level == 'A':
            from app.models.project import ProjectMember, Project
            is_member_of_projects = select(Project.id).outerjoin(ProjectMember, ProjectMember.project_id == Project.id).where(
                or_(
                    Project.owner_id == current_user.id,
                    Project.project_manager_id == current_user.id,
                    Project.delivery_head_id == current_user.id,
                    ProjectMember.user_id == current_user.id
                )
            )
            security_conds.append(Issue.project_id.in_(is_member_of_projects))
            
        issue_filter.append(or_(*security_conds))

    has_issue_assignees_table = select(1).select_from(issue_assignees).join(Issue, issue_assignees.c.issue_id == Issue.id).where(issue_assignees.c.user_id == User.id, *issue_filter)
    has_issue_followers_table = select(1).select_from(issue_followers).join(Issue, issue_followers.c.issue_id == Issue.id).where(issue_followers.c.user_id == User.id, *issue_filter)
    
    stmt = select(User).options(selectinload(User.role)).where(
        or_(
            select(1).select_from(Issue).where(Issue.assignee_id == User.id, *issue_filter).exists(),
            select(1).select_from(Issue).where(Issue.reporter_id == User.id, *issue_filter).exists(),
            has_issue_assignees_table.exists(),
            has_issue_followers_table.exists()
        )
    ).order_by(User.first_name, User.last_name)
    
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_issues(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
    project_id: Optional[int] = None,
    status_ids: Optional[List[int]] = None,
    priority_ids: Optional[List[int]] = None,
    severity_ids: Optional[List[int]] = None,
    assignee_emails: Optional[List[str]] = None,
    search: Optional[str] = None,
    sort_by: Optional[str] = None,
    milestone_id: Optional[int] = None,
    current_user=None,
    view_level: str = 'O',
    return_raw: bool = False
) -> dict:
    stmt = _issue_query()
    from app.models.master import MasterLookup
    count_stmt = select(
        func.count(Issue.id).label("total"),
        func.sum(case((MasterLookup.label.in_(["Completed", "Closed", "Resolved", "Cancelled", "Done", "Fixed"]), 1), else_=0)).label("closed"),
        func.sum(case((MasterLookup.label.in_(["In Progress", "In Review", "Testing", "To be Tested", "Reopened"]), 1), else_=0)).label("in_progress")
    ).outerjoin(MasterLookup, Issue.status_id == MasterLookup.id).where(Issue.is_deleted == False)

    if project_id is not None:
        stmt = stmt.where(Issue.project_id == project_id)
        count_stmt = count_stmt.where(Issue.project_id == project_id)
        
    if milestone_id is not None:
        stmt = stmt.where(Issue.milestone_id == milestone_id)
        count_stmt = count_stmt.where(Issue.milestone_id == milestone_id)

    if status_ids:
        stmt = stmt.where(Issue.status_id.in_(status_ids))
        count_stmt = count_stmt.where(Issue.status_id.in_(status_ids))

    if priority_ids:
        stmt = stmt.where(Issue.priority_id.in_(priority_ids))
        count_stmt = count_stmt.where(Issue.priority_id.in_(priority_ids))

    if severity_ids:
        stmt = stmt.where(Issue.severity_id.in_(severity_ids))
        count_stmt = count_stmt.where(Issue.severity_id.in_(severity_ids))

    if search:
        q = f"%{search}%"
        stmt = stmt.where(or_(Issue.bug_name.ilike(q), Issue.public_id.ilike(q)))
        count_stmt = count_stmt.where(or_(Issue.bug_name.ilike(q), Issue.public_id.ilike(q)))

    if assignee_emails:
        from app.models.issue import issue_assignees, issue_followers
        from app.models.user import User
        # Subqueries to match email
        has_assignee = select(1).select_from(issue_assignees).join(User, issue_assignees.c.user_id == User.id).where(issue_assignees.c.issue_id == Issue.id, User.email.in_(assignee_emails))
        has_follower = select(1).select_from(issue_followers).join(User, issue_followers.c.user_id == User.id).where(issue_followers.c.issue_id == Issue.id, User.email.in_(assignee_emails))
        
        stmt = stmt.where(
            or_(
                Issue.assignee.has(User.email.in_(assignee_emails)),
                Issue.reporter.has(User.email.in_(assignee_emails)),
                has_assignee.exists(),
                has_follower.exists()
            )
        )
        count_stmt = count_stmt.where(
            or_(
                Issue.assignee.has(User.email.in_(assignee_emails)),
                Issue.reporter.has(User.email.in_(assignee_emails)),
                has_assignee.exists(),
                has_follower.exists()
            )
        )

    # Apply Security Filter (RBAC) independently of UI filters
    if current_user and view_level != 'All':
        from app.models.issue import issue_assignees, issue_followers
        has_assignee_me = select(1).select_from(issue_assignees).where(issue_assignees.c.issue_id == Issue.id, issue_assignees.c.user_id == current_user.id).correlate(Issue)
        has_follower_me = select(1).select_from(issue_followers).where(issue_followers.c.issue_id == Issue.id, issue_followers.c.user_id == current_user.id).correlate(Issue)
        
        security_conds = [
            Issue.assignee_id == current_user.id,
            Issue.reporter_id == current_user.id,
            has_assignee_me.exists(),
            has_follower_me.exists()
        ]

        if view_level == 'A':
            from app.models.project import ProjectMember, Project
            is_member_of_projects = select(Project.id).outerjoin(ProjectMember, ProjectMember.project_id == Project.id).where(
                or_(
                    Project.owner_id == current_user.id,
                    Project.project_manager_id == current_user.id,
                    Project.delivery_head_id == current_user.id,
                    ProjectMember.user_id == current_user.id
                )
            )
            security_conds.append(Issue.project_id.in_(is_member_of_projects))
            
        cond = or_(*security_conds)
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    # Get total count
    stats_row = (await db.execute(count_stmt)).first()
    total = stats_row.total if stats_row and stats_row.total else 0
    closed = int(stats_row.closed) if stats_row and stats_row.closed else 0
    in_progress = int(stats_row.in_progress) if stats_row and stats_row.in_progress else 0
    open_count = total - closed - in_progress
    
    final_stats = {"open": open_count, "in_progress": in_progress, "closed": closed}
    active_count = total - closed

    if total == 0:
        return {"total": 0, "status_counts": {"active": 0, "completed": 0}, "items": [], "stats": final_stats}
    
    if sort_by and ':' in sort_by:
        field, order = sort_by.split(':', 1)
        sort_attr = getattr(Issue, field, None)
        if sort_attr is not None:
            if order == 'asc':
                stmt = stmt.order_by(sort_attr.asc(), Issue.id.desc())
            else:
                stmt = stmt.order_by(sort_attr.desc(), Issue.id.desc())
        else:
            stmt = stmt.order_by(Issue.id.desc())
    else:
        stmt = stmt.order_by(Issue.id.desc())

    # Execute data query
    from app.schemas.issue import IssueResponse
    result = await db.execute(stmt.offset(skip).limit(limit))
    issues = result.scalars().unique().all()
    
    if return_raw:
        return {"total": total, "status_counts": {"active": active_count, "completed": closed}, "items": list(issues), "stats": final_stats}

    items = []
    for i in issues:
        item = IssueResponse.model_validate(i).model_dump()
        items.append(item)

    return {"total": total, "status_counts": {"active": active_count, "completed": closed}, "items": items, "stats": final_stats}


async def create_issue(
    db: AsyncSession,
    issue: IssueCreate,
    actor_id: Optional[str] = None,
    created_by_id: Optional[int] = None,
) -> Issue:
    project = None
    if issue.project_id:
        project = (await db.execute(select(Project).where(Project.id == issue.project_id))).scalar_one_or_none()

    project_name = project.project_name if project else ""
    public_id = await get_next_sequence_id(db, Issue, project_name, issue.project_id, "BUG") if issue.project_id else generate_public_id("ISS-")

    db_issue = Issue(
        public_id          = public_id,
        bug_name           = issue.bug_name,
        description        = issue.description,
        project_id         = issue.project_id,
        milestone_id       = issue.milestone_id,
        associated_team_id = issue.associated_team_id,
        reporter_id        = issue.reporter_id,
        assignee_id        = issue.assignee_id,
        status_id          = issue.status_id,
        priority_id        = issue.priority_id,
        severity_id        = issue.severity_id,
        classification_id  = issue.classification_id,
        module             = issue.module,
        tags               = issue.tags,
        reproducible_flag  = issue.reproducible_flag,
        start_date         = issue.start_date,
        due_date           = issue.due_date,
        estimated_hours    = issue.estimated_hours,
    )

    if issue.reporter_email and not issue.reporter_id:
        r_user = (await db.execute(select(User).where(User.email == issue.reporter_email))).scalar_one_or_none()
        if r_user:
            db_issue.reporter_id = r_user.id

    if issue.follower_emails:
        followers = (await db.execute(select(User).where(User.email.in_(issue.follower_emails)))).scalars().all()
        db_issue.followers.extend(followers)

    if issue.assignee_emails:
        assignees = (await db.execute(select(User).where(User.email.in_(issue.assignee_emails)))).scalars().all()
        db_issue.assignees.extend(assignees)

    if issue.document_ids:
        docs = (await db.execute(select(Document).where(Document.id.in_(issue.document_ids)))).scalars().all()
        db_issue.documents.extend(docs)

    db.add(db_issue)
    await db.flush()

    await write_audit(
        db, actor_id, "CREATE", "issues",
        issue.project_id or db_issue.id, db_issue.id,
        [{"field_name": "bug_name", "old_value": None, "new_value": issue.bug_name}],
    )
    await db.commit()
    return await get_issue(db, db_issue.id)


async def bulk_create_issues(
    db: AsyncSession,
    issues: List[IssueCreate],
    actor_id: Optional[str] = None,
    created_by_id: Optional[int] = None,
) -> List[Issue]:
    project_ids = {i.project_id for i in issues if i.project_id}
    projects = (await db.execute(select(Project).where(Project.id.in_(project_ids)))).scalars().all() if project_ids else []
    project_map = {p.id: p.project_name for p in projects}

    all_emails = set()
    for i in issues:
        if i.reporter_email: all_emails.add(i.reporter_email)
        if i.follower_emails: all_emails.update(i.follower_emails)
        if i.assignee_emails: all_emails.update(i.assignee_emails)

    users = (await db.execute(select(User).where(User.email.in_(all_emails)))).scalars().all() if all_emails else []
    user_email_map = {u.email: u for u in users}

    all_doc_ids = set()
    for i in issues:
        if i.document_ids: all_doc_ids.update(i.document_ids)

    if all_doc_ids:
        docs = (await db.execute(select(Document).where(Document.id.in_(all_doc_ids)))).scalars().all()
        doc_map = {d.id: d for d in docs}
    else:
        doc_map = {}

    db_issues = []

    for issue in issues:
        project_name = project_map.get(issue.project_id, "")
        public_id = await get_next_sequence_id(db, Issue, project_name, issue.project_id, "BUG") if issue.project_id else generate_public_id("ISS-")

        db_issue = Issue(
            public_id          = public_id,
            bug_name           = issue.bug_name,
            description        = issue.description,
            project_id         = issue.project_id,
            milestone_id       = issue.milestone_id,
            associated_team_id = issue.associated_team_id,
            reporter_id        = issue.reporter_id,
            assignee_id        = issue.assignee_id,
            status_id          = issue.status_id,
            priority_id        = issue.priority_id,
            severity_id        = issue.severity_id,
            classification_id  = issue.classification_id,
            module             = issue.module,
            tags               = issue.tags,
            reproducible_flag  = issue.reproducible_flag,
            start_date         = issue.start_date,
            due_date           = issue.due_date,
            estimated_hours    = issue.estimated_hours,
        )

        if issue.reporter_email and not issue.reporter_id:
            r_user = user_email_map.get(issue.reporter_email)
            if r_user:
                db_issue.reporter_id = r_user.id

        if issue.follower_emails:
            followers = [user_email_map[e] for e in issue.follower_emails if e in user_email_map]
            db_issue.followers.extend(followers)

        if issue.assignee_emails:
            assignees = [user_email_map[e] for e in issue.assignee_emails if e in user_email_map]
            db_issue.assignees.extend(assignees)

        if issue.document_ids:
            docs_to_add = [doc_map[did] for did in issue.document_ids if did in doc_map]
            db_issue.documents.extend(docs_to_add)

        db_issues.append(db_issue)

    db.add_all(db_issues)
    await db.flush()

    for issue, db_issue in zip(issues, db_issues):
        await write_audit(
            db, actor_id, "CREATE", "issues",
            issue.project_id or db_issue.id, db_issue.id,
            [{"field_name": "bug_name", "old_value": None, "new_value": issue.bug_name}],
        )

    await db.commit()

    issue_ids = [i.id for i in db_issues]
    return (await db.execute(_issue_query().where(Issue.id.in_(issue_ids)))).scalars().unique().all()


async def update_issue(
    db: AsyncSession,
    issue_id: int,
    issue_update: IssueUpdate,
    actor_id: Optional[str] = None,
) -> Optional[Issue]:
    result = await db.execute(select(Issue).where(Issue.id == issue_id))
    db_issue = result.scalar_one_or_none()
    if not db_issue:
        return None

    update_data = issue_update.model_dump(
        exclude_unset=True,
        exclude={"assignee_emails", "follower_emails", "document_ids"},
    )

    if "status_id" in update_data and update_data["status_id"] != db_issue.status_id:
        update_data["previous_status_id"] = db_issue.status_id
        update_data["is_processed"] = False

    if "priority_id" in update_data and update_data["priority_id"] != db_issue.priority_id:
        update_data["is_processed"] = False

    if "severity_id" in update_data and update_data["severity_id"] != db_issue.severity_id:
        update_data["is_processed"] = False

    changes = capture_audit_details(db_issue, update_data)
    for key, value in update_data.items():
        setattr(db_issue, key, value)

    if issue_update.assignee_emails is not None:
        assignees = (await db.execute(select(User).where(User.email.in_(issue_update.assignee_emails)))).scalars().all()
        db_issue.assignees = list(assignees)

    if issue_update.follower_emails is not None:
        followers = (await db.execute(select(User).where(User.email.in_(issue_update.follower_emails)))).scalars().all()
        db_issue.followers = list(followers)

    if issue_update.document_ids is not None:
        docs = (await db.execute(select(Document).where(Document.id.in_(issue_update.document_ids)))).scalars().all()
        db_issue.documents = list(docs)

    db_issue.last_modified_time = datetime.now(timezone.utc)

    await write_audit(
        db, actor_id, "UPDATE", "issues",
        db_issue.project_id or issue_id, issue_id, changes,
    )
    await db.commit()
    return await get_issue(db, issue_id)


async def delete_issue(
    db: AsyncSession,
    issue_id: int,
    actor_id: Optional[str] = None,
) -> bool:
    result = await db.execute(select(Issue).where(Issue.id == issue_id))
    db_issue = result.scalar_one_or_none()
    if not db_issue:
        return False
    await write_audit(
        db, actor_id, "DELETE", "issues",
        db_issue.project_id or issue_id, issue_id,
        [{"field_name": "bug_name", "old_value": db_issue.bug_name, "new_value": None}],
    )
    await db.delete(db_issue)
    await db.commit()
    return True


async def search_issues(
    db: AsyncSession,
    query: str,
    project_id: Optional[int] = None,
    limit: int = 20,
) -> List[Issue]:
    if not query:
        return []
    q = f"%{query}%"
    stmt = _issue_query().where(or_(Issue.bug_name.ilike(q), Issue.public_id.ilike(q)))
    if project_id:
        stmt = stmt.where(Issue.project_id == project_id)
    result = await db.execute(stmt.limit(limit))
    return result.scalars().unique().all()
