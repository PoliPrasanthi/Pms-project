from datetime import datetime
from typing import List, Optional
import uuid

from sqlalchemy import ForeignKey, String, Text, Integer, BigInteger, Boolean, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.mssql import UNIQUEIDENTIFIER

from app.core.database import Base

class AuditFieldsMapping(Base):
    __tablename__ = "AuditFieldsMapping"

    ID: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    EntityName: Mapped[str] = mapped_column(String(150), nullable=False)
    FieldName: Mapped[str] = mapped_column(String(150), nullable=False)
    DisplayName: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    IsActive: Mapped[bool] = mapped_column(Boolean, nullable=False)
    IsVisible: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    OrderNo: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

class AuditLogs(Base):
    __tablename__ = "AuditLogs"

    ID: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    TableName: Mapped[str] = mapped_column(String(250), nullable=False)
    Action: Mapped[int] = mapped_column(Integer, nullable=False)
    PerformedBy: Mapped[uuid.UUID] = mapped_column(nullable=False)
    PerformedOn: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    RecordName: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    TransactionId: Mapped[uuid.UUID] = mapped_column(nullable=False)
    Comments: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ModuleName: Mapped[Optional[str]] = mapped_column(String(250), nullable=True)

    details: Mapped[List["AuditLogDetails"]] = relationship(
        back_populates="audit_log",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

class AuditLogDetails(Base):
    __tablename__ = "AuditLogDetails"

    Id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    AuditLogId: Mapped[int] = mapped_column(BigInteger, ForeignKey("AuditLogs.ID"), nullable=False)
    FieldName: Mapped[str] = mapped_column(String(250), nullable=False)
    OldValue: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    NewValue: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ValueType: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    audit_log: Mapped["AuditLogs"] = relationship(back_populates="details")

class AuditMetaDataInfo(Base):
    __tablename__ = "AuditMetaDataInfo"

    Id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    File_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    StartDate: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    EndDate: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ModuleOrEntityName: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    CreatedOn: Mapped[datetime] = mapped_column(DateTime, default=func.now(), server_default=func.now(), nullable=False)
