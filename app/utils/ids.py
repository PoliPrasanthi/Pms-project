from __future__ import annotations

import random
import string
from datetime import datetime
from typing import Type

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def generate_public_id(prefix: str, length: int = 6) -> str:
    chars = string.ascii_uppercase + string.digits
    random_str = ''.join(random.choices(chars, k=length))
    return f"{prefix}{random_str}"


import re

def get_project_initials(name: str) -> str:
    if not name:
        return "PRJ"
    clean_name = re.sub(r'[^a-zA-Z0-9\s]', '', name).strip()
    if not clean_name:
        return "PRJ"
    words = clean_name.split()
    if len(words) == 1:
        return words[0][:2].upper()
    return "".join(w[0] for w in words[:2]).upper()


async def get_next_project_id(db: AsyncSession, project_model) -> str:
    """Async-safe: generate sequential project public_id (e.g. PRJ-2026-001)."""
    year = datetime.now().year
    prefix = f"PRJ-{year}-"
    result = await db.execute(
        select(project_model.public_id)
        .where(project_model.public_id.like(f"{prefix}%"))
        .order_by(project_model.id.desc())
        .limit(1)
    )
    latest_id = result.scalar_one_or_none()
    if latest_id:
        try:
            num = int(latest_id.replace(prefix, ""))
        except ValueError:
            num = 0
    else:
        num = 0
    return f"{prefix}{num + 1:03d}"


async def get_next_sequence_id(
    db: AsyncSession,
    model_class,
    project_name: str,
    project_id: int,
    separator: str,
    is_padded: bool = False,
    model_name: str = "",
) -> str:
    """Async-safe: generate sequential public_id within a project (e.g. TSK-MA-1, BUG-MA-2)."""
    initials = get_project_initials(project_name)
    prefix = f"{separator}-{initials}-" if separator else f"{initials}-"

    stmt = select(model_class.public_id).where(model_class.public_id.like(f"{prefix}%"))
    if hasattr(model_class, "project_id"):
        stmt = stmt.where(model_class.project_id == project_id)

    result = await db.execute(stmt)
    all_ids = result.scalars().all()

    max_num = 0
    for pid in all_ids:
        if pid:
            val = pid.replace(prefix, "")
            try:
                n = int(val)
                if n > max_num:
                    max_num = n
            except ValueError:
                pass

    num = max_num + 1
    if is_padded:
        return f"{prefix}{num:03d}"
    return f"{prefix}{num}"
