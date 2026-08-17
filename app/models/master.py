from __future__ import annotations

from sqlalchemy import Boolean, Column, Integer, String, UniqueConstraint
from app.core.database import Base, AuditMixin

class MasterLookup(AuditMixin, Base):
    
    __tablename__ = "master_lookups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    category = Column(String(100), index=True, nullable=False)
    value = Column(String(100), nullable=False)
    label = Column(String(100), nullable=False)
    color = Column(String(50), nullable=True)
    icon = Column(String(50), nullable=True)
    order_index = Column(Integer, default=0, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    __table_args__ = (
        UniqueConstraint('category', 'value', name='uq_master_lookup_category_value'),
    )
