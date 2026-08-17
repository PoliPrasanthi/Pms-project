from __future__ import annotations

from enum import Enum
from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Enum as SAEnum, ForeignKey,
    Integer, Numeric, String, Text, UniqueConstraint
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base, AuditMixin
from sqlalchemy import Table, Column, Integer

project_stats_view = Table(
    "v_project_stats",
    Base.metadata,
    Column("project_id", Integer, ForeignKey("projects.id"), primary_key=True),
    Column("task_count", Integer, default=0),
    Column("completed_task_count", Integer, default=0),
    Column("issue_count", Integer, default=0),
    Column("milestone_count", Integer, default=0),
)

class ProjectStats(Base):
    __table__ = project_stats_view


class ProjectMember(Base):
    __tablename__ = "project_members"

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int]    = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)

    project_profile: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    portal_profile: Mapped[Optional[str]]  = mapped_column(String(100), nullable=True)
    role_in_project: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    invitation_status_id: Mapped[Optional[int]] = mapped_column(ForeignKey("master_lookups.id"), nullable=True)

    is_owner: Mapped[bool]                 = mapped_column(Boolean, default=False)

    is_processed: Mapped[bool]                        = mapped_column(Boolean, default=False)
    previous_invitation_status_id: Mapped[Optional[int]] = mapped_column(ForeignKey("master_lookups.id"), nullable=True)


    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), onupdate=func.now(), nullable=True)

    invitation_status_master = relationship("MasterLookup", foreign_keys=[invitation_status_id], lazy="selectin")
    
    @property
    def invitation_status(self) -> Optional[dict]:
        if self.invitation_status_master:
            return {
                "id": self.invitation_status_master.id,
                "value": self.invitation_status_master.value,
                "label": self.invitation_status_master.label,
                "color": self.invitation_status_master.color
            }
        return None

    user: Mapped["User"] = relationship(lazy="selectin")

    project: Mapped["Project"] = relationship(back_populates="team_members", lazy="selectin")


class Project(AuditMixin, Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id_sync: Mapped[str] = mapped_column(String(100), unique=True, index=True) 

    public_id: Mapped[str] = mapped_column(String(50), unique=True, index=True)

    account_name: Mapped[str]  = mapped_column(String(255))
    project_name: Mapped[str]  = mapped_column(String(255), unique=True, index=True)
    customer_name: Mapped[str] = mapped_column(String(255))
    client_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    
    billing_model: Mapped[str]            = mapped_column(String(100), nullable=False)
    project_type: Mapped[str]             = mapped_column(String(100), nullable=False)
    project_status_external: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    
    expected_start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    expected_end_date: Mapped[Optional[date]]   = mapped_column(Date, nullable=True)

    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    project_manager_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    delivery_head_id: Mapped[Optional[int]]   = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    template_id: Mapped[Optional[int]]        = mapped_column(ForeignKey("project_templates.id", ondelete="SET NULL"), nullable=True)

    status_id: Mapped[Optional[int]]   = mapped_column(ForeignKey("master_lookups.id"), nullable=True)
    priority_id: Mapped[Optional[int]] = mapped_column(ForeignKey("master_lookups.id"), nullable=True)


    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tags: Mapped[Optional[str]]        = mapped_column(String(500), nullable=True)

    estimated_hours: Mapped[float] = mapped_column(Numeric(10, 2), default=0.0)
    actual_hours: Mapped[float]    = mapped_column(Numeric(10, 2), default=0.0)

    actual_start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    actual_end_date: Mapped[Optional[date]]   = mapped_column(Date, nullable=True)


    ms_teams_group_id: Mapped[Optional[str]]   = mapped_column(String(255), nullable=True, index=True)
    ms_teams_channel_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    is_archived: Mapped[bool]  = mapped_column(Boolean, default=False)
    is_template: Mapped[bool]  = mapped_column(Boolean, default=False)
    is_group: Mapped[bool]     = mapped_column(Boolean, default=False)
    is_processed: Mapped[bool] = mapped_column(Boolean, default=False)

    previous_status_id: Mapped[Optional[int]] = mapped_column(ForeignKey("master_lookups.id", ondelete="SET NULL"), nullable=True)


    status_master   = relationship("MasterLookup", foreign_keys=[status_id], lazy="selectin")
    priority_master = relationship("MasterLookup", foreign_keys=[priority_id], lazy="selectin")

    @property
    def status(self) -> Optional[dict]:
        if self.status_master:
            return {
                "id": self.status_master.id,
                "value": self.status_master.value,
                "label": self.status_master.label,
                "color": self.status_master.color
            }
        return None

    @property
    def priority(self) -> Optional[dict]:
        if self.priority_master:
            return {
                "id": self.priority_master.id,
                "value": self.priority_master.value,
                "label": self.priority_master.label,
                "color": self.priority_master.color
            }
        return None



    project_manager = relationship("User", foreign_keys=[project_manager_id], lazy="selectin")
    delivery_head   = relationship("User", foreign_keys=[delivery_head_id], lazy="selectin")
    owner           = relationship("User", foreign_keys=[owner_id], lazy="selectin")

    source_template = relationship("ProjectTemplate", foreign_keys=[template_id], lazy="selectin")

    team_members: Mapped[List["ProjectMember"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", lazy="select"
    )

    tasks: Mapped[List["Task"]] = relationship("Task", back_populates="project", cascade="all, delete-orphan", lazy="select")
    issues: Mapped[List["Issue"]] = relationship("Issue", back_populates="project", cascade="all, delete-orphan", lazy="select")
    milestones: Mapped[List["Milestone"]] = relationship("Milestone", back_populates="project", cascade="all, delete-orphan", lazy="select")
    timelogs: Mapped[List["TimeLog"]] = relationship("TimeLog", back_populates="project", cascade="all, delete-orphan", lazy="select")
    task_lists: Mapped[List["TaskList"]] = relationship("TaskList", back_populates="project", cascade="all, delete-orphan", lazy="select")
    documents: Mapped[List["Document"]] = relationship("Document", back_populates="project", cascade="all, delete-orphan", lazy="select")

    # Mapped to view for performance
    stats: Mapped[Optional["ProjectStats"]] = relationship(
        "ProjectStats",
        primaryjoin="Project.id == ProjectStats.project_id",
        foreign_keys="[ProjectStats.project_id]",
        viewonly=True,
        lazy="select"
    )

    @property
    def task_count(self) -> int:
        if hasattr(self, '_dynamic_task_count'):
            return self._dynamic_task_count
        return self.stats.task_count if self.stats else 0
        
    @property
    def completed_task_count(self) -> int:
        if hasattr(self, '_dynamic_completed_task_count'):
            return self._dynamic_completed_task_count
        return self.stats.completed_task_count if self.stats else 0
        
    @property
    def issue_count(self) -> int:
        if hasattr(self, '_dynamic_issue_count'):
            return self._dynamic_issue_count
        return self.stats.issue_count if self.stats else 0
        
    @property
    def milestone_count(self) -> int:
        if hasattr(self, '_dynamic_milestone_count'):
            return self._dynamic_milestone_count
        return self.stats.milestone_count if self.stats else 0

    @property
    def completion_percentage(self) -> int:
        if hasattr(self, '_dynamic_completion_percentage'):
            return self._dynamic_completion_percentage
        tc = self.task_count
        cc = self.completed_task_count
        return round((cc / tc) * 100) if tc > 0 else 0
