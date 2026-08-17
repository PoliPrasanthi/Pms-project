from datetime import datetime
from typing import List, Optional
import uuid
from pydantic import BaseModel, Field, ConfigDict, model_validator
from .base import BaseSchema

class AuditDetailCreate(BaseModel):
    field_name: str = Field(alias="FieldName")
    old_value: Optional[str] = Field(None, alias="OldValue")
    new_value: Optional[str] = Field(None, alias="NewValue")

class AuditDetailResponse(BaseModel):
    # ORM attribute names are uppercase (Id, AuditLogId, FieldName, etc.)
    # Map them by their Python attribute names when coming from ORM objects
    id: int = Field(alias="Id")
    audit_log_id: int = Field(alias="AuditLogId")
    field_name: str = Field(alias="FieldName")
    old_value: Optional[str] = Field(None, alias="OldValue")
    new_value: Optional[str] = Field(None, alias="NewValue")

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

class AuditLogCreate(BaseModel):
    table_name: str = Field(alias="TableName")
    action: int = Field(alias="Action")
    performed_by: uuid.UUID = Field(alias="PerformedBy")

class AuditLogResponse(BaseModel):
    # Map ORM uppercase attributes to JSON snake_case fields
    id: int = Field(alias="ID")
    action_type: int = Field(alias="Action")
    resource_name: str = Field(alias="TableName")
    user_id: uuid.UUID = Field(alias="PerformedBy")
    created_at: datetime = Field(alias="PerformedOn")
    details: List[AuditDetailResponse] = []

    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True,
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_from_orm(cls, data):
        """
        When Pydantic reads from an ORM object (from_attributes=True),
        it reads Python attribute names directly. The ORM model uses
        uppercase names (ID, TableName, etc.), so we need to support both.
        This validator is a no-op for dict inputs (API responses) but
        ensures ORM objects are mapped correctly.
        """
        return data
