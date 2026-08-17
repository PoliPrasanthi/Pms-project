from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional, Union

from fastapi import Depends, HTTPException, status, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_sync_db, get_async_db
from sqlalchemy.ext.asyncio import AsyncSession

pwd_context    = CryptContext(schemes=["bcrypt"], deprecated="auto")
_bearer_scheme = HTTPBearer(auto_error=False)

ROLE_ADMIN           = settings.ROLE_ADMIN
ROLE_TEAM_LEAD       = settings.ROLE_TEAM_LEAD
ROLE_EMPLOYEE        = settings.ROLE_EMPLOYEE

FULL_ACCESS_ROLES     = [ROLE_ADMIN]
TEAM_LEAD_PLUS_ROLES  = [ROLE_ADMIN, ROLE_TEAM_LEAD, "Project Manager"]
ALL_ROLES             = [ROLE_ADMIN, ROLE_TEAM_LEAD, ROLE_EMPLOYEE, "Project Manager"]

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(
    subject: Union[str, Any],
    expires_delta: Optional[timedelta] = None,
) -> str:
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    return jwt.encode(
        {"exp": expire, "sub": str(subject)},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )

def create_refresh_token(
    subject: Union[str, Any],
) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    return jwt.encode(
        {"exp": expire, "sub": str(subject), "type": "refresh"},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )

async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_async_db),
):
    token_str = None
    if credentials:
        token_str = credentials.credentials
    elif token:
        token_str = token

    if not token_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload   = jwt.decode(token_str, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id_s = payload.get("sub")
        if not user_id_s:
            raise ValueError("No sub in payload")
        user_id = int(user_id_s)
    except (JWTError, ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    from app.models.user import User
    from sqlalchemy.orm import selectinload
    result = await db.execute(select(User).options(selectinload(User.role)).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user or user.is_deleted:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user

def _has_permission(user, perm_key: str) -> bool:
    """Check if a user has a given permission (boolean True or string 'O'/'A'/'All')."""
    if not user or not user.role or not user.role.permissions:
        return False
    perms = user.role.permissions
    if isinstance(perms, str):
        import json
        try:
            perms = json.loads(perms)
        except Exception:
            perms = {}
    if not isinstance(perms, dict):
        return False
    return normalize_view_level(perms.get(perm_key)) is not None

def normalize_view_level(val: Any) -> Optional[str]:
    """Standardize permission view levels to 'All', 'A', 'O', or None."""
    if val is None or val is False:
        return None
    if val is True or val == 'All' or str(val).lower() == 'all':
        return 'All'
    val_str = str(val).strip()
    if val_str in ('O', 'Owned', 'Own', 'owner'):
        return 'O'
    if val_str in ('A', 'Assigned', 'Involved', 'Member', 'assigned'):
        return 'A'
    return None

def get_user_view_level(user, perm_key: str) -> Optional[str]:
    """Get the view level for a permission: 'O', 'A', 'All', or None."""
    if not user or not user.role:
        return None
    if user.role.name == ROLE_ADMIN:
        return 'All'
        
    # Enforce personalized view (Owned/Assigned) for all users except Admin and Project Manager
    if user.role.name not in ('Admin', 'Project Manager'):
        if user.role.name == 'Team Lead':
            return 'A'
        if perm_key in ('task-view', 'issue-view', 'time-view'):
            return 'O'
        return 'A'
        
    perms = user.role.permissions
    if not perms:
        return None
    if isinstance(perms, str):
        import json
        try:
            perms = json.loads(perms)
        except Exception:
            perms = {}
    if not isinstance(perms, dict):
        return None
    val = perms.get(perm_key)
    return normalize_view_level(val)

def is_full_access(user) -> bool:
    if not user or not user.role: return False
    if user.role.name == ROLE_ADMIN: return True
    return _has_permission(user, 'settings-edit')

def is_team_lead_plus(user) -> bool:
    if not user or not user.role: return False
    if user.role.name in [ROLE_ADMIN, ROLE_TEAM_LEAD]: return True
    return _has_permission(user, 'report-view') or _has_permission(user, 'settings-edit')

def get_user_role_name(user) -> Optional[str]:
    return user.role.name if user and user.role else None

def is_employee_only(user) -> bool:
    return get_user_role_name(user) == ROLE_EMPLOYEE

class RoleChecker:
    def __init__(self, allowed_roles: List[str], required_permission: str = None):
        self.allowed_roles = allowed_roles
        self.required_permission = required_permission

    def __call__(self, current_user=Depends(get_current_user)):
        if current_user.role and current_user.role.name == ROLE_ADMIN:
            return current_user
            
        if self.required_permission and current_user.role and current_user.role.permissions:
            perm_val = current_user.role.permissions.get(self.required_permission)
            # Accept boolean True or string values ('O', 'A', 'All') as valid permissions
            if perm_val is True or perm_val in ('O', 'A', 'All'):
                return current_user

        if current_user.role and current_user.role.name in self.allowed_roles:
            return current_user
            
        msg = f"Access denied. Requires one of: {', '.join(self.allowed_roles)}"
        if self.required_permission:
            msg += f" or permission: {self.required_permission}"
            
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=msg,
        )

allow_pm             = RoleChecker(FULL_ACCESS_ROLES)
allow_team_lead_plus = RoleChecker(TEAM_LEAD_PLUS_ROLES)
allow_all_roles      = RoleChecker(ALL_ROLES)
allow_proj_create    = RoleChecker(FULL_ACCESS_ROLES, "proj-create")
allow_proj_view      = RoleChecker(ALL_ROLES, "proj-view")
allow_task_create    = RoleChecker(TEAM_LEAD_PLUS_ROLES, "task-create")
allow_task_view      = RoleChecker(ALL_ROLES, "task-view")
allow_issue_create   = RoleChecker(TEAM_LEAD_PLUS_ROLES, "issue-create")
allow_issue_view     = RoleChecker(ALL_ROLES, "issue-view")
allow_issue_delete   = RoleChecker(TEAM_LEAD_PLUS_ROLES, "issue-delete")
allow_task_delete    = RoleChecker(TEAM_LEAD_PLUS_ROLES, "task-delete")
allow_proj_delete    = RoleChecker(FULL_ACCESS_ROLES, "proj-delete")
allow_milestone_create = RoleChecker(TEAM_LEAD_PLUS_ROLES, "milestone-create")
allow_milestone_view   = RoleChecker(ALL_ROLES, "milestone-view")
allow_milestone_edit   = RoleChecker(TEAM_LEAD_PLUS_ROLES, "milestone-edit")
allow_milestone_delete = RoleChecker(FULL_ACCESS_ROLES, "milestone-delete")

allow_time_create    = RoleChecker(ALL_ROLES, "time-create")
allow_time_view      = RoleChecker(ALL_ROLES, "time-view")
allow_time_edit      = RoleChecker(ALL_ROLES, "time-edit")
allow_time_delete    = RoleChecker(TEAM_LEAD_PLUS_ROLES, "time-delete")

allow_user_view      = RoleChecker(ALL_ROLES, "user-view")
allow_user_create    = RoleChecker(TEAM_LEAD_PLUS_ROLES, "user-create")
allow_user_edit      = RoleChecker(TEAM_LEAD_PLUS_ROLES, "user-edit")
allow_user_delete    = RoleChecker(FULL_ACCESS_ROLES, "user-delete")

allow_team_view      = RoleChecker(ALL_ROLES, "team-view")
allow_team_create    = RoleChecker(TEAM_LEAD_PLUS_ROLES, "team-create")
allow_team_edit      = RoleChecker(TEAM_LEAD_PLUS_ROLES, "team-edit")
allow_team_delete    = RoleChecker(FULL_ACCESS_ROLES, "team-delete")

allow_report_view    = RoleChecker(TEAM_LEAD_PLUS_ROLES, "report-view")
allow_settings_view  = RoleChecker(TEAM_LEAD_PLUS_ROLES, "settings-view")
allow_role_manage    = RoleChecker(FULL_ACCESS_ROLES, "settings-edit")

def allow_authenticated(current_user=Depends(get_current_user)):
    return current_user

class CheckProjectOwner:
    def __init__(self, allowed_roles: List[str], required_permission: str = None):
        self.allowed_roles = allowed_roles
        self.required_permission = required_permission

    def __call__(
        self,
        project_id: int,
        current_user=Depends(get_current_user),
        db: Session = Depends(get_sync_db),
    ):
        if current_user.role and current_user.role.name == ROLE_ADMIN:
            return current_user

        from app.models.project import Project

        result   = db.execute(select(Project.owner_id).where(Project.id == project_id))
        owner_id = result.scalar_one_or_none()
        if owner_id is not None and owner_id == current_user.id:
            return current_user

        if self.required_permission and current_user.role and current_user.role.permissions:
            perm_val = current_user.role.permissions.get(self.required_permission)
            if perm_val is True or perm_val in ('O', 'A', 'All'):
                return current_user


        if current_user.role and current_user.role.name in self.allowed_roles:
            return current_user

        msg = f"Access denied. Requires project ownership or one of: {', '.join(self.allowed_roles)}"
        if self.required_permission:
            msg += f" or permission: {self.required_permission}"

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=msg,
        )

class CheckTaskOwner:
    def __init__(self, allowed_roles: List[str], required_permission: str = None):
        self.allowed_roles = allowed_roles
        self.required_permission = required_permission
        
    def __call__(
        self,
        task_id: int,
        current_user=Depends(get_current_user),
        db: Session = Depends(get_sync_db),
    ):
        if current_user.role and current_user.role.name == ROLE_ADMIN:
            return current_user

        from app.models.task import Task, task_assignees
        from app.models.project import Project
        from sqlalchemy import exists
        
        result = db.execute(
            select(Task.assignee_id, Project.owner_id)
            .outerjoin(Project, Project.id == Task.project_id)
            .where(Task.id == task_id)
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Task not found")
            
        assignee_id, owner_id = row
        
        if owner_id is not None and owner_id == current_user.id:
            return current_user
        if assignee_id is not None and assignee_id == current_user.id:
            return current_user

        is_co_assignee = db.execute(
            select(exists().where(
                task_assignees.c.task_id == task_id,
                task_assignees.c.user_id == current_user.id
            ))
        ).scalar()
        if is_co_assignee:
            return current_user

        from app.models.project import ProjectMember
        is_member = db.execute(
            select(exists().where(
                ProjectMember.project_id == (db.execute(select(Task.project_id).where(Task.id == task_id)).scalar()),
                ProjectMember.user_id == current_user.id
            ))
        ).scalar()
        if is_member:
            return current_user

        if self.required_permission and current_user.role and current_user.role.permissions:
            perm_val = current_user.role.permissions.get(self.required_permission)
            if perm_val is True or perm_val in ('O', 'A', 'All'):
                return current_user

        if current_user.role and current_user.role.name in self.allowed_roles:
            return current_user

        msg = f"Access denied. Requires assignee, project ownership or one of: {', '.join(self.allowed_roles)}"
        if self.required_permission:
            msg += f" or permission: {self.required_permission}"

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=msg,
        )

class CheckIssueOwner:
    def __init__(self, allowed_roles: List[str], required_permission: str = None):
        self.allowed_roles = allowed_roles
        self.required_permission = required_permission
        
    def __call__(
        self,
        issue_id: int,
        current_user=Depends(get_current_user),
        db: Session = Depends(get_sync_db),
    ):
        if current_user.role and current_user.role.name == ROLE_ADMIN:
            return current_user

        from app.models.issue import Issue, issue_assignees
        from app.models.project import Project
        from sqlalchemy import exists
        
        result = db.execute(
            select(Issue.assignee_id, Issue.reporter_id, Project.owner_id)
            .outerjoin(Project, Project.id == Issue.project_id)
            .where(Issue.id == issue_id)
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Issue not found")
            
        assignee_id, reporter_id, owner_id = row
        
        if owner_id is not None and owner_id == current_user.id:
            return current_user
        if assignee_id is not None and assignee_id == current_user.id:
            return current_user
        if reporter_id is not None and reporter_id == current_user.id:
            return current_user

        is_co_assignee = db.execute(
            select(exists().where(
                issue_assignees.c.issue_id == issue_id,
                issue_assignees.c.user_id == current_user.id
            ))
        ).scalar()
        if is_co_assignee:
            return current_user

        from app.models.project import ProjectMember
        is_member = db.execute(
            select(exists().where(
                ProjectMember.project_id == (db.execute(select(Issue.project_id).where(Issue.id == issue_id)).scalar()),
                ProjectMember.user_id == current_user.id
            ))
        ).scalar()
        if is_member:
            return current_user

        if self.required_permission and current_user.role and current_user.role.permissions:
            perm_val = current_user.role.permissions.get(self.required_permission)
            if perm_val is True or perm_val in ('O', 'A', 'All'):
                return current_user

        if current_user.role and current_user.role.name in self.allowed_roles:
            return current_user

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Requires assignee, reporter, project ownership or elevated permissions.",
        )


check_project_owner_or_pm       = CheckProjectOwner(FULL_ACCESS_ROLES, "proj-edit")
check_project_owner_or_lead     = CheckProjectOwner(TEAM_LEAD_PLUS_ROLES, "proj-edit")
check_task_owner_or_lead        = CheckTaskOwner(TEAM_LEAD_PLUS_ROLES, "task-edit")
check_issue_owner_or_lead       = CheckIssueOwner(TEAM_LEAD_PLUS_ROLES, "issue-edit")


class ProjectRoleChecker:
    def __init__(self, allowed_roles: List[str]):
        self.allowed_roles = allowed_roles

    def __call__(
        self,
        project_id: int,
        current_user=Depends(get_current_user),
        db: Session = Depends(get_sync_db),
    ):
        if current_user.role and current_user.role.name in FULL_ACCESS_ROLES:
            return current_user

        from app.models.project import ProjectMember
        assignment = (
            db.execute(
                select(ProjectMember).where(
                    ProjectMember.project_id == project_id,
                    ProjectMember.user_id    == current_user.id,
                )
            )
        ).scalar_one_or_none()

        if not assignment:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You are not a member of this project.")

        if not assignment.project_profile:
            if "Member" in self.allowed_roles:
                return current_user
        else:
            if assignment.project_profile in self.allowed_roles:
                return current_user

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Requires project-specific role: {', '.join(self.allowed_roles)}",
        )

allow_project_lead   = ProjectRoleChecker([settings.PROFILE_PROJECT_LEAD])
allow_project_member = ProjectRoleChecker([settings.PROFILE_PROJECT_LEAD, settings.PROFILE_DEVELOPER, settings.PROFILE_MEMBER])
