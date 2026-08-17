from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional

from app.core.database import get_sync_db
from app.core.security import get_current_user, allow_authenticated

def require_authenticated_user(current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )
    return current_user

def get_current_user_email(current_user=Depends(get_current_user)) -> str:

    return current_user.email

def get_current_user_id(current_user=Depends(get_current_user)) -> int:
    return current_user.id

def get_current_o365_id(current_user=Depends(get_current_user)) -> Optional[str]:

    return current_user.o365_id or str(current_user.id)

def auto_populate_timelog(payload, current_user):
    
    if not getattr(payload, "user_id", None):
        payload.user_id = current_user.id
    return payload

def auto_populate_milestone(payload, current_user):
    if not getattr(payload, "owner_id", None):
        payload.owner_id = current_user.id
    return payload
