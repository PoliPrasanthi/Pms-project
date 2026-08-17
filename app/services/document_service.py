from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.document import Document
from app.schemas.document import DocumentCreate, DocumentUpdate
from app.utils.ids import generate_public_id
from app.utils.audit_utils import write_audit, capture_audit_details


async def get_document(db: AsyncSession, document_id: int) -> Optional[Document]:
    result = await db.execute(
        select(Document).options(joinedload(Document.uploaded_by)).where(Document.id == document_id)
    )
    return result.scalar_one_or_none()


async def get_documents(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
    project_id: Optional[int] = None,
    file_type: Optional[str] = None,
) -> dict:
    query = select(Document)
    if project_id is not None:
        query = query.where(Document.project_id == project_id)
        # NOTE: previously this filtered out documents attached to issues via
        # `~Document.issues.any()` — that was incorrect. All project documents
        # should be visible regardless of issue association.
    if file_type:
        query = query.where(Document.file_type.ilike(f"%{file_type}%"))
        
    count_stmt = query.with_only_columns(func.count(Document.id)).order_by(None)
    total = (await db.execute(count_stmt)).scalar() or 0

    query = query.options(joinedload(Document.uploaded_by))

    result = await db.execute(query.order_by(Document.created_at.desc()).offset(skip).limit(limit))
    items = result.scalars().unique().all()
    return {"total": total, "items": items}


async def create_document(
    db: AsyncSession,
    document: DocumentCreate,
    uploaded_by_email: Optional[str] = None,
    actor_id: Optional[str] = None,
) -> Document:
    public_id = generate_public_id("DOC-")
    db_document = Document(
        public_id        = public_id,
        title            = document.title,
        description      = document.description,
        file_url         = document.file_url,
        file_type        = document.file_type,
        file_size        = document.file_size,
        project_id       = document.project_id,
        uploaded_by_email = uploaded_by_email,
    )
    db.add(db_document)
    await db.flush()

    await write_audit(db, actor_id, "CREATE", "documents",
                resource_id=document.project_id or db_document.id,
                record_id=db_document.id,
                details=[{"field_name": "title", "old_value": None, "new_value": document.title}])

    await db.commit()
    return await get_document(db, db_document.id)


async def update_document(
    db: AsyncSession,
    document_id: int,
    document_update: DocumentUpdate,
    actor_id: Optional[str] = None,
) -> Optional[Document]:
    db_document = (await db.execute(select(Document).where(Document.id == document_id))).scalar_one_or_none()
    if not db_document:
        return None

    update_data = document_update.model_dump(exclude_unset=True)
    changes = capture_audit_details(db_document, update_data)

    for key, value in update_data.items():
        setattr(db_document, key, value)

    await write_audit(db, actor_id, "UPDATE", "documents",
                resource_id=db_document.project_id or document_id,
                record_id=document_id,
                details=changes)

    await db.commit()
    return await get_document(db, db_document.id)


async def delete_document(
    db: AsyncSession,
    document_id: int,
    actor_id: Optional[str] = None,
) -> bool:
    db_document = (await db.execute(select(Document).where(Document.id == document_id))).scalar_one_or_none()
    if db_document:
        await write_audit(db, actor_id, "DELETE", "documents",
                    resource_id=db_document.project_id or document_id,
                    record_id=document_id,
                    details=[{"field_name": "title", "old_value": db_document.title, "new_value": None}])
        await db.delete(db_document)
        await db.commit()
        return True
    return False
