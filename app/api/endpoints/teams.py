from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.core.database import get_async_db
from app.core.security import allow_authenticated
from app.schemas.team import TeamCreate, TeamResponse, TeamUpdate, TeamWithMembersResponse, TeamListResponse
from app.services import team_service

router = APIRouter(dependencies=[Depends(allow_authenticated)])

@router.post("/", response_model=TeamResponse)
async def create_team(team: TeamCreate, db: AsyncSession = Depends(get_async_db), current_user = Depends(allow_authenticated)):
    return await team_service.create_team(db=db, team=team, actor_id=current_user.o365_id or str(current_user.id))

@router.get("/", response_model=TeamListResponse)
async def read_teams(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_async_db)):
    return await team_service.get_teams(db, skip=skip, limit=limit)

@router.get("/search", response_model=List[TeamResponse])
async def search_teams(q: str = Query("", min_length=0), limit: int = 20, db: AsyncSession = Depends(get_async_db)):
    return await team_service.search_teams(db, query=q, limit=limit)

@router.get("/{team_id}", response_model=TeamWithMembersResponse)
async def read_team(team_id: int, db: AsyncSession = Depends(get_async_db)):
    db_team = await team_service.get_team(db, team_id=team_id)
    if db_team is None:
        raise HTTPException(status_code=404, detail="Team not found")
    return db_team

@router.put("/{team_id}", response_model=TeamResponse)
async def update_team(team_id: int, team_update: TeamUpdate, db: AsyncSession = Depends(get_async_db), current_user = Depends(allow_authenticated)):
    db_team = await team_service.update_team(db, team_id=team_id, team_update=team_update, actor_id=current_user.o365_id or str(current_user.id))
    if db_team is None:
        raise HTTPException(status_code=404, detail="Team not found")
    return db_team

@router.delete("/{team_id}", status_code=204)
async def delete_team(team_id: int, db: AsyncSession = Depends(get_async_db), current_user = Depends(allow_authenticated)):
    success = await team_service.delete_team(db, team_id=team_id, actor_id=current_user.o365_id or str(current_user.id))
    if not success:
        raise HTTPException(status_code=404, detail="Team not found")

@router.post("/{team_id}/members/{user_email}")
async def add_team_member(team_id: int, user_email: str, db: AsyncSession = Depends(get_async_db), current_user = Depends(allow_authenticated)):
    success = await team_service.add_team_member(db, team_id=team_id, user_email=user_email, actor_id=current_user.o365_id or str(current_user.id))
    if not success:
        raise HTTPException(status_code=400, detail="Could not add user to team. Check if user and team exist.")
    return {"message": "Member added successfully"}

@router.delete("/{team_id}/members/{user_email}")
async def remove_team_member(team_id: int, user_email: str, db: AsyncSession = Depends(get_async_db), current_user = Depends(allow_authenticated)):
    success = await team_service.remove_team_member(db, team_id=team_id, user_email=user_email, actor_id=current_user.o365_id or str(current_user.id))
    if not success:
        raise HTTPException(status_code=400, detail="Could not remove user from team. Check if user is in team.")
    return {"message": "Member removed successfully"}
