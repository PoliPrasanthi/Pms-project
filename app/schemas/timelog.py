from __future__ import annotations

from datetime import date as dt_date, datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.schemas.user import UserBase


class TimeLogCreate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    log_title: Optional[str] = None


    user_id: Optional[int] = None

    project_id: Optional[int] = None
    task_id: Optional[int]    = None
    issue_id: Optional[int]   = None

    date: dt_date
    daily_log_hours: float
    time_period: Optional[str] = None
    notes: Optional[str] = None

    billing_type: Optional[str]     = "Billable"
    approval_status_id: Optional[int] = None
    general_log: Optional[bool]     = False



class TimeLogUpdate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    log_title: Optional[str]        = None
    date: Optional[dt_date]            = None
    daily_log_hours: Optional[float] = None
    time_period: Optional[str]      = None
    notes: Optional[str]            = None
    billing_type: Optional[str]     = None
    approval_status_id: Optional[int]  = None

    project_id: Optional[int] = None
    task_id: Optional[int]    = None
    issue_id: Optional[int]   = None

    previous_approval_status_id: Optional[int] = None
    is_processed: Optional[bool]            = None


class TimeLogProjectSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_name: str
    project_id_sync: Optional[str] = None

class TimeLogTaskSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    task_name: str
    public_id: Optional[str] = None

class TimeLogIssueSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    bug_name: str
    public_id: Optional[str] = None

class TimeLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    public_id: Optional[str] = None
    log_title: Optional[str]

    @field_validator('log_title', 'notes', mode='before')
    @classmethod
    def strip_html_tags(cls, v: Optional[str]) -> Optional[str]:
        """Strip HTML markup (e.g. '<div><br></div>') from text fields."""
        from app.utils.html_utils import strip_html
        return strip_html(v)

    user_id: int
    created_by_id: Optional[int]

    project_id: Optional[int]
    task_id: Optional[int]
    issue_id: Optional[int]

    date: dt_date
    daily_log_hours: float
    time_period: Optional[str]
    notes: Optional[str]

    billing_type: str
    approval_status_id: Optional[int]
    general_log: bool


    is_processed: bool                      = False
    previous_approval_status_id: Optional[int] = None


    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    user: Optional[UserBase]        = None
    created_by: Optional[UserBase]  = None
    
    project: Optional[TimeLogProjectSchema] = None
    task: Optional[TimeLogTaskSchema]       = None
    issue: Optional[TimeLogIssueSchema]     = None


class TimeLogBulkCreate(BaseModel):
    logs: List[TimeLogCreate]

class TimeLogListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    total: int
    status_counts: Optional[dict] = None
    items: List[TimeLogResponse]
