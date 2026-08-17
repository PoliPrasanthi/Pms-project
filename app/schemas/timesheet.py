from pydantic import BaseModel, field_validator
from typing import Optional, List
from datetime import date
from decimal import Decimal
from typing import Any
from .user import UserBase

class TimesheetBase(BaseModel):
    name: str
    start_date: date
    end_date: date
    billing_type: Optional[str] = "Billable"
    total_hours: Optional[Decimal] = Decimal('0.0')
    approval_status: Optional[str] = "Pending"

class TimesheetCreate(TimesheetBase):
    project_id: int
    user_email: str

class TimesheetUpdate(BaseModel):
    name: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    billing_type: Optional[str] = None
    total_hours: Optional[Decimal] = None
    approval_status: Optional[str] = None

class TimesheetResponse(TimesheetBase):
    id: int
    project_id: int
    user_email: str

    project: Optional[Any] = None
    user: Optional[UserBase] = None

    @field_validator('name', mode='before')
    @classmethod
    def strip_html_tags(cls, v: Optional[str]) -> Optional[str]:
        """Strip HTML markup from name."""
        from app.utils.html_utils import strip_html
        return strip_html(v)

    model_config = {"from_attributes": True}
