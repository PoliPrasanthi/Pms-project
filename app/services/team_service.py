from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.team import Team
from app.models.user import User
from app.schemas.team import TeamCreate, TeamUpdate
from app.utils.ids import generate_public_id
from app.utils.audit_utils import capture_audit_details, write_audit


def _team_query():
    return select(Team).options(selectinload(Team.members), selectinload(Team.lead))


async def get_team(db: AsyncSession, team_id: int) -> Optional[Team]:
    result = await db.execute(_team_query().where(Team.id == team_id))
    return result.scalar_one_or_none()


async def get_teams(db: AsyncSession, skip: int = 0, limit: int = 100) -> dict:
    stmt = _team_query()
    count_stmt = select(func.count(Team.id))
    total = (await db.execute(count_stmt)).scalar() or 0

    result = await db.execute(stmt.offset(skip).limit(limit))
    items = result.scalars().unique().all()
    return {"total": total, "items": items}


async def search_teams(db: AsyncSession, query: str = "", limit: int = 15) -> List[Team]:
    stmt = _team_query()
    if query:
        stmt = stmt.where(Team.name.ilike(f"%{query}%"))
    result = await db.execute(stmt.limit(limit))
    return result.scalars().unique().all()


async def create_team(
    db: AsyncSession,
    team: TeamCreate,
    actor_id: Optional[str] = None,
) -> Team:
    public_id = generate_public_id("TM-")
    db_team = Team(
        public_id                    = public_id,
        name                         = team.name,
        team_email                   = team.team_email,
        budget_allocation            = team.budget_allocation,
        description                  = team.description,
        team_type                    = team.team_type,
        max_team_size                = team.max_team_size,
        primary_communication_channel = team.primary_communication_channel,
        channel_id                   = team.channel_id,
        lead_email                   = team.lead_email,
    )

    if team.member_emails:
        members = (await db.execute(select(User).where(User.email.in_(team.member_emails)))).scalars().all()
        db_team.members = list(members)

    db.add(db_team)
    await db.flush()

    await write_audit(
        db, actor_id, "CREATE", "teams", db_team.id, db_team.id,
        [{"field_name": "name", "old_value": None, "new_value": team.name}],
    )
    await db.commit()
    return await get_team(db, db_team.id)


async def update_team(
    db: AsyncSession,
    team_id: int,
    team_update: TeamUpdate,
    actor_id: Optional[str] = None,
) -> Optional[Team]:
    result = await db.execute(select(Team).where(Team.id == team_id))
    db_team = result.scalar_one_or_none()
    if not db_team:
        return None

    update_data = team_update.model_dump(exclude_unset=True)
    changes = capture_audit_details(db_team, update_data)

    member_emails = update_data.pop("member_emails", None)
    if member_emails is not None:
        members = (await db.execute(select(User).where(User.email.in_(member_emails)))).scalars().all()
        db_team.members = list(members)

    for key, value in update_data.items():
        setattr(db_team, key, value)

    await write_audit(db, actor_id, "UPDATE", "teams", team_id, team_id, changes)
    await db.commit()
    return await get_team(db, team_id)


async def delete_team(
    db: AsyncSession,
    team_id: int,
    actor_id: Optional[str] = None,
) -> bool:
    result = await db.execute(select(Team).where(Team.id == team_id))
    db_team = result.scalar_one_or_none()
    if not db_team:
        return False
    await write_audit(
        db, actor_id, "DELETE", "teams", team_id, team_id,
        [{"field_name": "name", "old_value": db_team.name, "new_value": None}],
    )
    await db.delete(db_team)
    await db.commit()
    return True


async def add_team_member(
    db: AsyncSession,
    team_id: int,
    user_email: str,
    actor_id: Optional[str] = None,
) -> bool:
    db_team = (await db.execute(select(Team).options(selectinload(Team.members)).where(Team.id == team_id))).scalar_one_or_none()
    db_user = (await db.execute(select(User).where(User.email == user_email))).scalar_one_or_none()

    if db_team and db_user and db_user not in db_team.members:
        db_team.members.append(db_user)
        await write_audit(
            db, actor_id, "ASSIGN_TO_TEAM", "teams", team_id, team_id,
            [{"field_name": "members", "old_value": None, "new_value": user_email}],
        )
        await db.commit()
        return True
    return False


async def remove_team_member(
    db: AsyncSession,
    team_id: int,
    user_email: str,
    actor_id: Optional[str] = None,
) -> bool:
    db_team = (await db.execute(select(Team).options(selectinload(Team.members)).where(Team.id == team_id))).scalar_one_or_none()
    db_user = (await db.execute(select(User).where(User.email == user_email))).scalar_one_or_none()

    if db_team and db_user and db_user in db_team.members:
        db_team.members.remove(db_user)
        await write_audit(
            db, actor_id, "REMOVE_FROM_TEAM", "teams", team_id, team_id,
            [{"field_name": "members", "old_value": user_email, "new_value": None}],
        )
        await db.commit()
        return True
    return False
