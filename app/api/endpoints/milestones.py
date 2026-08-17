from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional

from app.core.database import get_async_db
from app.core.security import allow_authenticated, allow_milestone_create, allow_milestone_view, allow_milestone_edit, allow_milestone_delete
from app.core.dependencies import auto_populate_milestone
from app.schemas.milestone import MilestoneCreate, MilestoneResponse, MilestoneUpdate, MilestoneListResponse
from app.services import milestone_service

router = APIRouter(dependencies=[Depends(allow_authenticated)])

@router.post("/", response_model=MilestoneResponse)
async def create_milestone(
    milestone: MilestoneCreate,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_milestone_create),
):
    auto_populate_milestone(milestone, current_user)
    return await milestone_service.create_milestone(
        db=db,
        milestone=milestone,
        actor_id=current_user.o365_id or str(current_user.id),
    )

@router.get("/", response_model=MilestoneListResponse)
async def read_milestones(
    project_id: Optional[int] = None,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_milestone_view),
):
    from app.core.security import get_user_view_level
    view_level = get_user_view_level(current_user, 'proj-view')
    return await milestone_service.get_milestones(
        db,
        skip=skip,
        limit=limit,
        project_id=project_id,
        current_user=current_user if view_level not in ('All', None) else None,
        view_level=view_level,
    )

@router.get("/search", response_model=List[MilestoneResponse])
async def search_milestones(
    q: str = Query(..., min_length=1),
    project_id: Optional[int] = None,
    limit: int = Query(50, gt=0, le=100),
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_milestone_view),
):
    from sqlalchemy import select, or_
    from app.models.milestone import Milestone
    from app.services.milestone_service import _batch_enrich_milestones, _milestone_query
    from app.core.security import get_user_view_level, normalize_view_level
    
    stmt = _milestone_query().where(Milestone.milestone_name.ilike(f"%{q}%"))
    if project_id:
        stmt = stmt.where(Milestone.project_id == project_id)
        
    view_level = get_user_view_level(current_user, 'proj-view')
    if view_level not in ('All', None):
        norm_level = normalize_view_level(view_level)
        if norm_level == "O":
            stmt = stmt.where(
                or_(
                    Milestone.owner_id == current_user.id,
                    Milestone.project.has(
                        or_(
                            Project.owner_id == current_user.id,
                            Project.project_manager_id == current_user.id
                        )
                    )
                )
            )
        elif norm_level == "A":
            proj_stmt = select(Project.id).where(
                Project.is_deleted == False,
                or_(
                    Project.owner_id == current_user.id,
                    Project.project_manager_id == current_user.id,
                    Project.delivery_head_id == current_user.id,
                    Project.team_members.any(ProjectMember.user_id == current_user.id)
                )
            )
            allowed_project_ids = (await db.execute(proj_stmt)).scalars().all()
            if allowed_project_ids:
                stmt = stmt.where(
                    or_(
                        Milestone.owner_id == current_user.id,
                        Milestone.project_id.in_(allowed_project_ids)
                    )
                )
            else:
                stmt = stmt.where(Milestone.owner_id == current_user.id)

    milestones = list((await db.execute(stmt.limit(limit))).scalars().unique().all())
    await _batch_enrich_milestones(db, milestones)
    return milestones


@router.get("/{milestone_id}", response_model=MilestoneResponse)
async def read_milestone(
    milestone_id: int,
    db: AsyncSession = Depends(get_async_db),
):
    db_milestone = await milestone_service.get_milestone(db, milestone_id=milestone_id)
    if db_milestone is None:
        raise HTTPException(status_code=404, detail="Milestone not found")
    return db_milestone

@router.put("/{milestone_id}", response_model=MilestoneResponse)
async def update_milestone(
    milestone_id: int,
    milestone_in: MilestoneUpdate,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_milestone_edit),
):
    updated = await milestone_service.update_milestone(
        db,
        milestone_id,
        milestone_in,
        actor_id=current_user.o365_id or str(current_user.id),
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Milestone not found")
    return updated

@router.delete("/{milestone_id}", status_code=204)
async def delete_milestone(
    milestone_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(allow_milestone_delete),
):
    success = await milestone_service.delete_milestone(
        db,
        milestone_id=milestone_id,
        actor_id=current_user.o365_id or str(current_user.id),
    )
    if not success:
        raise HTTPException(status_code=404, detail="Milestone not found")
