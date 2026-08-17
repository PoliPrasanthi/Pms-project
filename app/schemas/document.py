from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class DocumentBase(BaseModel):
    title: str
    description: Optional[str] = None
    file_url: str
    file_type: Optional[str] = "url"
    file_size: Optional[int] = 0
    project_id: int

class DocumentCreate(DocumentBase):
    pass

class DocumentUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    file_url: Optional[str] = None
    file_type: Optional[str] = None

class DocumentUser(BaseModel):
    id: int
    first_name: str
    last_name: str

    class Config:
        from_attributes = True

class DocumentResponse(DocumentBase):
    id: int
    public_id: str
    uploaded_by_email: Optional[str] = None
    uploaded_by: Optional[DocumentUser] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class DocumentListResponse(BaseModel):
    total: int
    items: list[DocumentResponse]
