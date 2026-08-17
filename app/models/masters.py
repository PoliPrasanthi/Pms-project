from sqlalchemy import Column, Integer, String
from app.core.database import Base, AuditMixin

class UserStatus(AuditMixin, Base):
    __tablename__ = "user_statuses"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), unique=True, index=True, nullable=False)

class Skill(AuditMixin, Base):
    __tablename__ = "skills"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, index=True, nullable=False)

class Status(AuditMixin, Base):
    __tablename__ = "statuses"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), unique=True, index=True, nullable=False)

class Priority(AuditMixin, Base):
    __tablename__ = "priorities"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), unique=True, index=True, nullable=False)
