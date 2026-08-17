from pydantic import BaseModel, EmailStr, computed_field
from typing import Optional, List
from datetime import date
from .masters import RoleResponse, MasterResponse, SkillResponse
from .base import BaseSchema
from app.core.config import settings

class UserBase(BaseSchema):
    id: Optional[int] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: EmailStr
    username: Optional[str] = None
    role: Optional[RoleResponse] = None
    is_external: Optional[bool] = False
    is_synced: Optional[bool] = False
    display_name: Optional[str] = None
    gender: Optional[str] = None
    country: Optional[str] = None
    state: Optional[str] = None
    language: Optional[str] = settings.DEFAULT_LANGUAGE
    timezone: Optional[str] = None

    model_config = {"from_attributes": True}

    @computed_field
    @property
    def name(self) -> str:
        parts = []
        if self.first_name:
            parts.append(self.first_name)
        if self.last_name:
            parts.append(self.last_name)
        return " ".join(parts) if parts else (self.username or self.email.split('@')[0])

class UserCreate(UserBase):
    employee_id: Optional[str] = None
    phone: Optional[str] = None
    job_title: Optional[str] = None
    join_date: Optional[date] = None
    role_id: Optional[int] = None
    status_id: Optional[int] = None
    skill_ids: Optional[List[int]] = []
    o365_id: Optional[str] = None
    manager_email: Optional[str] = None

class UserUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    job_title: Optional[str] = None
    join_date: Optional[date] = None
    role_id: Optional[int] = None
    status_id: Optional[int] = None
    skill_ids: Optional[List[int]] = None
    o365_id: Optional[str] = None
    is_external: Optional[bool] = None
    is_synced: Optional[bool] = None
    display_name: Optional[str] = None
    gender: Optional[str] = None
    country: Optional[str] = None
    state: Optional[str] = None
    language: Optional[str] = None
    timezone: Optional[str] = None
    manager_email: Optional[str] = None

class UserResponse(UserBase):
    id: int
    public_id: str
    employee_id: str
    phone: Optional[str] = None
    job_title: Optional[str] = None
    join_date: Optional[date] = None
    role_id: Optional[int] = None
    status_id: Optional[int] = None
    manager_email: Optional[str] = None
    o365_id: Optional[str] = None
    display_name: Optional[str] = None
    gender: Optional[str] = None
    country: Optional[str] = None
    state: Optional[str] = None
    language: Optional[str] = None
    timezone: Optional[str] = None

    role: Optional[RoleResponse] = None
    status: Optional[MasterResponse] = None
    manager: Optional[UserBase] = None
    skills: List[SkillResponse] = []

    model_config = {"from_attributes": True}

class RoleWithUsersResponse(RoleResponse):
    users: List[UserResponse] = []

class UserListResponse(BaseModel):
    total: int
    items: List[UserResponse]
