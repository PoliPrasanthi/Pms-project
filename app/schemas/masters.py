from pydantic import BaseModel
from typing import Dict, Any, Optional, List

class MasterBase(BaseModel):
    name: str

class MasterCreate(MasterBase):
    pass

class MasterResponse(MasterBase):
    id: int

    model_config = {"from_attributes": True}

class MasterLookupBase(BaseModel):
    category: str
    value: str
    label: str
    color: Optional[str] = None
    icon: Optional[str] = None
    order_index: int = 0
    is_active: bool = True

class MasterLookupResponse(MasterLookupBase):
    id: int
    model_config = {"from_attributes": True}

class RoleBase(BaseModel):
    name: str
    description: Optional[str] = None
    permissions: Dict[str, Any] = {}

class RoleCreate(RoleBase):
    user_ids: Optional[List[int]] = []

class RoleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    permissions: Optional[Dict[str, Any]] = None
    user_ids: Optional[List[int]] = None

class BulkRolePermissionsUpdate(BaseModel):
    role_permissions: Dict[int, Dict[str, Any]]

class RoleResponse(RoleBase):
    id: int
    users_count: Optional[int] = 0

    model_config = {"from_attributes": True}

class SkillBase(BaseModel):
    name: str

class SkillCreate(SkillBase):
    pass

class SkillResponse(SkillBase):
    id: int

    model_config = {"from_attributes": True}
