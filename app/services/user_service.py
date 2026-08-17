from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.user import User
from app.models.masters import Skill
from app.models.roles import Role
from app.schemas.user import UserCreate, UserUpdate
from app.utils.ids import generate_public_id
from app.utils.audit_utils import capture_audit_details, write_audit


def _user_query():
    return (
        select(User)
        .options(
            selectinload(User.role),
            selectinload(User.status),
            selectinload(User.skills),
            selectinload(User.manager),
        )
    )


async def get_user(db: AsyncSession, user_id: int) -> Optional[User]:
    result = await db.execute(_user_query().where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_user_by_email(db: AsyncSession, email: str) -> Optional[User]:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_user_by_username(db: AsyncSession, username: str) -> Optional[User]:
    result = await db.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def get_users(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 20,
    search: Optional[str] = None,
    role_ids: Optional[List[int]] = None,
    is_active: Optional[bool] = None,
) -> dict:
    stmt = select(User)

    if search:
        q = f"%{search}%"
        stmt = stmt.where(
            or_(
                User.first_name.ilike(q),
                User.last_name.ilike(q),
                User.email.ilike(q),
                User.username.ilike(q),
                User.display_name.ilike(q),
            )
        )
    if role_ids:
        stmt = stmt.where(User.role_id.in_(role_ids))
    if is_active is not None:
        stmt = stmt.where(User.is_active == is_active)

    count_stmt = stmt.with_only_columns(func.count(User.id.distinct())).order_by(None)
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = stmt.options(
        selectinload(User.role),
        selectinload(User.status),
        selectinload(User.skills),
        selectinload(User.manager),
    )
    items = (await db.execute(stmt.offset(skip).limit(limit))).scalars().unique().all()
    return {"total": total, "items": items}


async def create_user(
    db: AsyncSession,
    user: UserCreate,
    actor_id: Optional[str] = None,
) -> User:
    public_id = generate_public_id("USR-")
    db_user = User(
        public_id    = public_id,
        employee_id  = user.employee_id or generate_public_id("EMP-"),
        first_name   = user.first_name,
        last_name    = user.last_name,
        email        = user.email,
        username     = user.username or user.email.split("@")[0],
        o365_id      = user.o365_id,
        phone        = user.phone,
        job_title    = user.job_title,
        join_date    = user.join_date,
        role_id      = user.role_id,
        status_id    = user.status_id,
        manager_email = user.manager_email,
        display_name = user.display_name,
        gender       = user.gender,
        country      = user.country,
        state        = user.state,
        language     = user.language,
        timezone     = user.timezone,
    )

    if user.skill_ids:
        skills = (await db.execute(select(Skill).where(Skill.id.in_(user.skill_ids)))).scalars().all()
        db_user.skills.extend(skills)

    db.add(db_user)
    await db.flush()

    await write_audit(
        db, actor_id, "CREATE", "users", db_user.id, db_user.id,
        [{"field_name": "email", "old_value": None, "new_value": user.email}],
    )
    await db.commit()
    return await get_user(db, db_user.id)


async def update_user(
    db: AsyncSession,
    user_id: int,
    user_update: UserUpdate,
    actor_id: Optional[str] = None,
) -> Optional[User]:
    result = await db.execute(select(User).where(User.id == user_id))
    db_user = result.scalar_one_or_none()
    if not db_user:
        return None

    update_data = user_update.model_dump(exclude_unset=True)
    changes = capture_audit_details(db_user, update_data)

    skill_ids = update_data.pop("skill_ids", None)
    if skill_ids is not None:
        skills = (await db.execute(select(Skill).where(Skill.id.in_(skill_ids)))).scalars().all()
        db_user.skills = list(skills)

    for key, value in update_data.items():
        setattr(db_user, key, value)

    await write_audit(db, actor_id, "UPDATE", "users", user_id, user_id, changes)
    await db.commit()
    return await get_user(db, user_id)


async def delete_user(
    db: AsyncSession,
    user_id: int,
    actor_id: Optional[str] = None,
) -> bool:
    result = await db.execute(select(User).where(User.id == user_id))
    db_user = result.scalar_one_or_none()
    if not db_user:
        return False
    await write_audit(
        db, actor_id, "DELETE", "users", user_id, user_id,
        [{"field_name": "email", "old_value": db_user.email, "new_value": None}],
    )
    await db.delete(db_user)
    await db.commit()
    return True


async def search_users(db: AsyncSession, query: Optional[str] = None, limit: int = 20, is_active: Optional[bool] = None) -> List[User]:
    stmt = _user_query().where(User.is_deleted == False)
    if query:
        q = f"%{query}%"
        stmt = stmt.where(
            or_(
                User.first_name.ilike(q),
                User.last_name.ilike(q),
                User.email.ilike(q),
                User.username.ilike(q),
                User.display_name.ilike(q),
            )
        )
    if is_active is not None:
        stmt = stmt.where(User.is_active == is_active)
        
    result = await db.execute(stmt.limit(limit))
    return result.scalars().unique().all()


# ---------------------------------------------------------------------------
# Synchronous audit helper — used only by the sync SSO upsert path below.
# The standard write_audit() is async and cannot be awaited from a sync
# function that runs inside run_in_threadpool.
# ---------------------------------------------------------------------------
def _write_audit_sync(
    db,
    actor_id: Optional[str],
    action_type: str,   # "CREATE" | "UPDATE"
    user_id: int,
    details: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """Write an AuditLogs + AuditLogDetails row synchronously.

    Runs inside a SAVEPOINT so a failure here never rolls back the caller's
    transaction (mirrors the behaviour of the async write_audit helper).
    """
    from app.models.audit import AuditLogs, AuditLogDetails
    import logging

    action_map = {"CREATE": 1, "UPDATE": 2, "DELETE": 3}
    action_int = action_map.get(action_type.upper(), 2)

    # Resolve actor UUID (mirrors _resolve_actor_uuid in audit_utils)
    if not actor_id or actor_id == "system":
        performed_by = uuid.UUID(int=0)
    else:
        try:
            performed_by = uuid.UUID(actor_id)
        except (ValueError, AttributeError):
            try:
                performed_by = uuid.UUID(int=int(actor_id))
            except (ValueError, TypeError):
                performed_by = uuid.UUID(int=0)

    try:
        with db.begin_nested():  # SAVEPOINT
            audit_log = AuditLogs(
                TableName     = "users",
                Action        = action_int,
                PerformedBy   = performed_by,
                PerformedOn   = datetime.now(timezone.utc).replace(tzinfo=None),
                TransactionId = uuid.uuid4(),
                Comments      = f"SSO {action_type} on Record ID: {user_id}",
                ModuleName    = "SSO",
            )
            db.add(audit_log)
            db.flush()

            if details:
                db.add_all([
                    AuditLogDetails(
                        AuditLogId = audit_log.ID,
                        FieldName  = str(d.get("field_name", ""))[:250],
                        OldValue   = str(d["old_value"]) if d.get("old_value") is not None else None,
                        NewValue   = str(d["new_value"]) if d.get("new_value") is not None else None,
                        ValueType  = 1,
                    )
                    for d in details
                ])
    except Exception:
        logging.getLogger("app.audit").warning(
            "Sync audit write failed for SSO action=%s user_id=%s — savepoint rolled back.",
            action_type, user_id,
            exc_info=True,
        )


# upsert_o365_user remains sync — called via run_in_threadpool from auth.py
def upsert_o365_user(
    db,
    o365_id: str,
    email: Optional[str],
    first_name: str,
    last_name: str,
    display_name: Optional[str] = None,
) -> User:
    if not o365_id:
        raise ValueError("o365_id is required for SSO upsert")

    from sqlalchemy import select as sync_select

    user = db.execute(sync_select(User).where(User.o365_id == o365_id)).scalar_one_or_none()

    if not user and email:
        user = db.execute(sync_select(User).where(User.email == email.lower())).scalar_one_or_none()

    if user:
        # Capture field changes before modifying the user for audit purposes
        audit_changes = []
        for field, new_val in [
            ("o365_id", o365_id),
            ("first_name", first_name),
            ("last_name", last_name),
            ("display_name", display_name),
        ]:
            if new_val is not None:
                old_val = getattr(user, field, None)
                if str(old_val) != str(new_val):
                    audit_changes.append({"field_name": field, "old_value": old_val, "new_value": new_val})

        user.o365_id = o365_id
        user.is_synced = True

        if not user.role_id:
            default_role = db.execute(sync_select(Role).where(Role.name == "Employee")).scalar_one_or_none()
            if default_role:
                user.role_id = default_role.id

        if not user.status_id:
            from app.models.masters import UserStatus
            default_status = db.execute(sync_select(UserStatus).where(UserStatus.name == "Active")).scalar_one_or_none()
            if default_status:
                user.status_id = default_status.id

        if first_name is not None: user.first_name = first_name
        if last_name is not None: user.last_name = last_name
        if display_name is not None: user.display_name = display_name

        # Audit the SSO profile sync (only writes if something actually changed)
        if audit_changes:
            _write_audit_sync(db, o365_id, "UPDATE", user.id, audit_changes)

        db.commit()
        db.refresh(user)
        return user

    if not email:
        raise ValueError("Email is required to create a new SSO user record.")

    default_role = db.execute(sync_select(Role).where(Role.name == "Employee")).scalar_one_or_none()

    import uuid
    base_username = email.split("@")[0].lower().replace(".", "_")
    username = base_username
    if db.execute(sync_select(User.id).where(User.username == username)).scalar_one_or_none():
        username = f"{base_username}_{uuid.uuid4().hex[:8]}"

    new_user = User(
        public_id    = generate_public_id("USR-"),
        employee_id  = generate_public_id("EMP-"),
        first_name   = first_name or email.split("@")[0],
        last_name    = last_name or "",
        email        = email.lower(),
        username     = username,
        display_name = display_name or f"{first_name} {last_name}".strip(),
        o365_id      = o365_id,
        is_synced    = True,
        is_external  = False,
        role_id      = default_role.id if default_role else None,
    )

    db.add(new_user)
    try:
        db.flush()  # Populate new_user.id before writing the audit row
        # Audit the SSO user creation
        _write_audit_sync(
            db, o365_id, "CREATE", new_user.id,
            [{"field_name": "email", "old_value": None, "new_value": email}],
        )
        db.commit()
        db.refresh(new_user)
    except Exception as e:
        db.rollback()
        raise e

    return new_user
