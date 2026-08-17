from sqlalchemy import Column, Integer, String, Date, Numeric, ForeignKey
from sqlalchemy.orm import relationship
from app.core.database import Base, AuditMixin

class Timesheet(AuditMixin, Base):
    __tablename__ = "timesheets"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    user_email = Column(String(255), ForeignKey("users.email"), nullable=False)
    billing_type = Column(String(50), default="Billable")
    total_hours = Column(Numeric(5, 2), default=0.0)
    approval_status = Column(String(50), default="Pending")

    project = relationship("Project", lazy="selectin")
    user = relationship("User", foreign_keys=[user_email], lazy="selectin")
    timelogs = relationship("TimeLog", back_populates="timesheet", lazy="select")
