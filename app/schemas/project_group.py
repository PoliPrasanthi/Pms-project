from pydantic import BaseModel
from typing import Optional

class ProjectGroupBase(BaseModel):
    name: str
    description: Optional[str] = None

class ProjectGroupCreate(ProjectGroupBase):
    pass

class ProjectGroupUpdate(ProjectGroupBase):
    name: Optional[str] = None

class ProjectGroupResponse(ProjectGroupBase):
    id: int

    class Config:
        from_attributes = True

class ProjectGroupListResponse(BaseModel):
    total: int
    items: list[ProjectGroupResponse]
