from sqlalchemy import Column, Integer, String, Text, ForeignKey, Table, DateTime, Boolean
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.core.database import AuditMixin, Base

issue_document_link = Table(
    "issue_document_link",
    Base.metadata,
    Column("issue_id", Integer, ForeignKey("issues.id", ondelete="CASCADE"), primary_key=True),
    Column("document_id", Integer, ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
    Column("created_at", DateTime(timezone=True), default=func.now(), server_default=func.now(), nullable=False),
    Column("updated_at", DateTime(timezone=True), default=None, onupdate=func.now(), nullable=True),
    Column("is_active", Boolean, default=True, nullable=False),
    Column("is_deleted", Boolean, default=False, nullable=False)
)

class Document(AuditMixin, Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    public_id = Column(String(50), unique=True, index=True, nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    file_url = Column(String(1024), nullable=False)
    file_type = Column(String(100), nullable=True)
    file_size = Column(Integer, nullable=True)

    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)

    uploaded_by_email = Column(String(255), ForeignKey("users.email", ondelete="SET NULL"), nullable=True)

    project = relationship("Project", back_populates="documents", lazy="selectin")
    uploaded_by = relationship("User", foreign_keys=[uploaded_by_email], lazy="selectin")
    issues = relationship("Issue", secondary=issue_document_link, back_populates="documents", lazy="select")
