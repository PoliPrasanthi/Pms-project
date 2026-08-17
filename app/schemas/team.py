from pydantic import BaseModel, EmailStr
from typing import Optional, List
from .masters import MasterResponse
from .user import UserResponse, UserBase

class TeamBase(BaseModel):
    name: str
    team_email: EmailStr
    budget_allocation: float = 0.0
    description: Optional[str] = None
    team_type: Optional[str] = None
    max_team_size: Optional[int] = None
    primary_communication_channel: Optional[str] = None
    channel_id: Optional[str] = None

class TeamCreate(TeamBase):
    lead_email: Optional[str] = None
    member_emails: Optional[List[str]] = []

class TeamUpdate(BaseModel):
    name: Optional[str] = None
    team_email: Optional[EmailStr] = None
    budget_allocation: Optional[float] = None
    description: Optional[str] = None
    team_type: Optional[str] = None
    max_team_size: Optional[int] = None
    primary_communication_channel: Optional[str] = None
    channel_id: Optional[str] = None
    lead_email: Optional[str] = None
    member_emails: Optional[List[str]] = None

class TeamResponse(TeamBase):
    id: int
    public_id: str
    lead_email: Optional[str] = None

    lead: Optional[UserBase] = None
    members_count: int = 0

    model_config = {"from_attributes": True}

class TeamWithMembersResponse(TeamResponse):
    members: List[UserResponse] = []

class TeamListResponse(BaseModel):
    total: int
    items: List[TeamResponse]
