from __future__ import annotations

from datetime import date
from typing import List, Optional

from sqlalchemy import (
    Boolean, Column, Date, ForeignKey, Integer,
    String, Text
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, AuditMixin
from sqlalchemy import Table, Column, Integer

milestone_stats_view = Table(
    "v_milestone_stats",
    Base.metadata,
    Column("milestone_id", Integer, ForeignKey("milestones.id"), primary_key=True),
    Column("task_count", Integer, default=0),
    Column("completed_task_count", Integer, default=0),
    Column("issue_count", Integer, default=0),
)

class MilestoneStats(Base):
    __table__ = milestone_stats_view

class Milestone(AuditMixin, Base):
    __tablename__ = "milestones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(String(50), unique=True, index=True)

    milestone_name: Mapped[str] = mapped_column(String(255), index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    project_id: Mapped[Optional[int]] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    owner_id: Mapped[Optional[int]]   = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    status_id: Mapped[Optional[int]]   = mapped_column(ForeignKey("master_lookups.id"), nullable=True)
    priority_id: Mapped[Optional[int]] = mapped_column(ForeignKey("master_lookups.id"), nullable=True)
    
    flags: Mapped[Optional[str]]  = mapped_column(String(50), nullable=True)



    tags: Mapped[Optional[str]]   = mapped_column(String(500), nullable=True)

    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[Optional[date]]   = mapped_column(Date, nullable=True)

    completion_percentage: Mapped[Optional[int]] = mapped_column(Integer, default=0, nullable=True)
    
    is_processed: Mapped[bool] = mapped_column(Boolean, default=False)
    previous_status_id: Mapped[Optional[int]] = mapped_column(ForeignKey("master_lookups.id", ondelete="SET NULL"), nullable=True)


    status_master   = relationship("MasterLookup", foreign_keys=[status_id], lazy="selectin")
    priority_master = relationship("MasterLookup", foreign_keys=[priority_id], lazy="selectin")

    @property
    def status(self) -> Optional[dict]:
        if hasattr(self, '_dynamic_status') and self._dynamic_status:
            return self._dynamic_status
        if self.status_master:
            return {
                "id": self.status_master.id,
                "value": self.status_master.value,
                "label": self.status_master.label,
                "color": self.status_master.color
            }
        pct = getattr(self, "completion_percentage", 0) or 0
        if pct >= 100:
            return {
                "id": self.status_id or 4,
                "value": "completed",
                "label": "Completed",
                "color": "#10b981"
            }
        elif pct > 0:
            return {
                "id": self.status_id or 2,
                "value": "in_progress",
                "label": "In Progress",
                "color": "#3b82f6"
            }
        if self.status_id:
            return {
                "id": self.status_id,
                "value": "active",
                "label": "Active",
                "color": "#22c55e"
            }
        return {
            "id": 0,
            "value": "active",
            "label": "Active",
            "color": "#22c55e"
        }

    @property
    def priority(self) -> Optional[dict]:
        if self.priority_master:
            return {
                "id": self.priority_master.id,
                "value": self.priority_master.value,
                "label": self.priority_master.label,
                "color": self.priority_master.color
            }
        if self.priority_id:
            return {
                "id": self.priority_id,
                "value": "medium",
                "label": "Medium",
                "color": "#3b82f6"
            }
        return {
            "id": 0,
            "value": "medium",
            "label": "Medium",
            "color": "#3b82f6"
        }

    project = relationship("Project", back_populates="milestones", lazy="selectin")
    _owner_rel = relationship("User", foreign_keys=[owner_id], lazy="selectin")
    task_lists = relationship("TaskList", back_populates="milestone", cascade="all, delete-orphan", lazy="select")

    @property
    def owner(self):
        if hasattr(self, '_dynamic_owner') and self._dynamic_owner:
            return self._dynamic_owner
        return self._owner_rel

    @owner.setter
    def owner(self, value):
        self._owner_rel = value

    @property
    def milestone_owner(self):
        return self.owner

    stats: Mapped[Optional["MilestoneStats"]] = relationship(
        "MilestoneStats",
        primaryjoin="Milestone.id == MilestoneStats.milestone_id",
        foreign_keys="[MilestoneStats.milestone_id]",
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
