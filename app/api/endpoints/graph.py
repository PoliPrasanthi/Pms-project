from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Dict, Any

from app.services.graph_service import search_azure_users
from app.core.security import allow_user_view
from app.core.database import get_async_db

router = APIRouter()

@router.get(
    "/search-users",
    response_model=List[Dict[str, Any]],
    dependencies=[Depends(allow_user_view)],
)
async def search_users(
    q: str = Query(..., min_length=2, description="Search Entra ID by displayName or mail"),
    db: AsyncSession = Depends(get_async_db),
):
    return search_azure_users(q, db=db)
