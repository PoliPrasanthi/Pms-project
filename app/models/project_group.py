from sqlalchemy import Column, Integer, String, Text
from sqlalchemy.orm import relationship
from app.core.database import Base, AuditMixin

class ProjectGroup(AuditMixin, Base):
    __tablename__ = "project_groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), index=True, nullable=False, unique=True)
    description = Column(Text, nullable=True)
