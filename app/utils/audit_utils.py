from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text


def capture_audit_details(
    old_obj: Any,
    new_data_dict: Dict[str, Any],
) -> List[Dict[str, Optional[str]]]:
    changes: List[Dict[str, Optional[str]]] = []
    for field, new_value in new_data_dict.items():
        old_value = getattr(old_obj, field, None)
        old_str = str(old_value) if old_value is not None else None
        new_str = str(new_value) if new_value is not None else None
        if old_str != new_str:
            changes.append(
                {"field_name": field, "old_value": old_str, "new_value": new_str}
            )
    return changes


def _resolve_actor_uuid(actor_id: Optional[str]) -> uuid.UUID:
    """
    Convert actor_id to a UUID safely.

    Supports:
    - Real UUID strings (e.g. Azure AD o365_id: "9f993c04-433c-4799-8964-ceb340e6df61")
    - Plain integer strings (e.g. JWT sub: "42") → encoded into UUID low bits
    - None / "system" → zero UUID
    """
    if not actor_id or actor_id == "system":
        return uuid.UUID(int=0)
    try:
        return uuid.UUID(actor_id)
    except (ValueError, AttributeError):
        pass
    # Fallback: integer user-id encoded as UUID low 32-bits
    try:
        user_int = int(actor_id)
        return uuid.UUID(int=user_int)
    except (ValueError, TypeError):
        return uuid.UUID(int=0)


async def write_audit(
    db,
    actor_id: Optional[str],
    action_type: str,
    resource_name: str,
    resource_id: int,
    record_id: int,
    details: Optional[List[Dict[str, Optional[str]]]] = None,
) -> None:
    """
    Write an audit log entry inside a SAVEPOINT so that a failure here
    never rolls back the parent transaction.
    """
    from app.models.audit import AuditLogs, AuditLogDetails

    action_map = {"CREATE": 1, "UPDATE": 2, "DELETE": 3, "ASSIGN": 4, "REMOVE": 5}
    action_verb = str(action_type).upper().split("_")[0]
    action_int = action_map.get(action_verb, 5)

    performed_by = _resolve_actor_uuid(actor_id)

    try:
        # Use a savepoint so an audit failure never corrupts the parent transaction
        async with db.begin_nested():
            audit_log = AuditLogs(
                TableName     = resource_name[:250],
                Action        = action_int,
                PerformedBy   = performed_by,
                PerformedOn   = datetime.now(timezone.utc).replace(tzinfo=None),
                TransactionId = uuid.uuid4(),
                Comments      = f"Action: {action_type} on Record ID: {record_id}",
                ModuleName    = "System",
            )
            db.add(audit_log)
            await db.flush()

            if details:
                db.add_all([
                    AuditLogDetails(
                        AuditLogId = audit_log.ID,
                        FieldName  = str(d.get("field_name", ""))[:250],
                        OldValue   = str(d["old_value"]) if d.get("old_value") is not None else None,
                        NewValue   = str(d["new_value"]) if d.get("new_value") is not None else None,
                        ValueType  = 1,
                    )
                    for d in details
                ])
    except Exception:
        from logging import getLogger
        getLogger("app.audit").warning(
            "Audit write failed for action=%s resource=%s record=%s — "
            "savepoint rolled back, parent transaction intact.",
            action_type, resource_name, record_id,
            exc_info=True,
        )
