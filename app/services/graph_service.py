from msal import ConfidentialClientApplication
from requests import get
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session

from app.core.config import settings

def get_graph_token() -> str:

    if not settings.AZURE_CLIENT_ID or not settings.AZURE_CLIENT_SECRET or not settings.AZURE_TENANT_ID:
        raise ValueError(
            "Azure SPN credentials are not configured. "
            "Set AZURE_TENANT_ID, AZURE_CLIENT_ID, and AZURE_CLIENT_SECRET in .env."
        )

    authority = f"{settings.MS_LOGIN_BASE_URL}/{settings.AZURE_TENANT_ID}"
    msal_app = ConfidentialClientApplication(
        settings.AZURE_CLIENT_ID,
        authority=authority,
        client_credential=settings.AZURE_CLIENT_SECRET,
    )

    result = msal_app.acquire_token_silent(
        [f"{settings.MS_GRAPH_BASE_URL}/.default"], account=None
    )
    if not result:
        result = msal_app.acquire_token_for_client(
            scopes=[f"{settings.MS_GRAPH_BASE_URL}/.default"]
        )

    if "access_token" in result:
        return result["access_token"]

    raise Exception(
        f"Failed to acquire Graph token: {result.get('error_description', 'Unknown error')}"
    )

def _jit_upsert_user(db: Session, graph_user: Dict[str, Any]) -> Optional[int]:

    from app.models.user import User
    from app.utils.ids import generate_public_id

    oid          = graph_user.get("id")
    mail         = graph_user.get("mail") or graph_user.get("userPrincipalName", "")
    display_name = graph_user.get("displayName", "")

    if not oid or not mail:
        return None

    if "#ext#" in mail.lower() or "technorucs" not in mail.lower().split("@")[-1]:
        return None

    try:
        existing = db.query(User).filter(User.o365_id == oid).first()
        if existing:
            stale = (
                existing.display_name != display_name
                or existing.email != mail
            )
            if stale:
                existing.display_name = display_name
                existing.email        = mail
                db.flush()
            return existing.id

        existing_by_email = db.query(User).filter(User.email == mail).first()
        if existing_by_email:
            existing_by_email.o365_id     = oid
            existing_by_email.display_name = display_name
            existing_by_email.is_synced   = True
            db.flush()
            return existing_by_email.id

        name_parts = display_name.split(" ", 1)
        first_name = name_parts[0]
        last_name  = name_parts[1] if len(name_parts) > 1 else None

        new_user = User(
            public_id    = generate_public_id("USR-"),
            employee_id  = generate_public_id("EMP-"),
            o365_id      = oid,
            email        = mail,
            username     = mail.split("@")[0],
            first_name   = first_name,
            last_name    = last_name,
            display_name = display_name,
            is_synced    = True,
        )
        db.add(new_user)
        db.flush()
        return new_user.id

    except Exception as exc:
        print(f"[JIT UPSERT WARNING] Could not provision user {mail}: {exc}")
        return None

def search_azure_users(
    query: str,
    db: Optional[Session] = None,
) -> List[Dict[str, Any]]:

    token = get_graph_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
        "ConsistencyLevel": "eventual",
    }

    safe_query = query.replace("'", "''")

    odata_filter = (
        f"startswith(displayName,'{safe_query}') or "
        f"startswith(mail,'{safe_query}') or "
        f"startswith(userPrincipalName,'{safe_query}')"
    )

    url = (
        f"{settings.MS_GRAPH_BASE_URL}/v1.0/users"
        f"?$filter={odata_filter}"
        f"&$select=id,displayName,mail"
        f"&$top=10"
        f"&$count=true"
    )

    response = get(url, headers=headers)

    if response.status_code != 200:
        print(f"[GRAPH API ERROR] {response.status_code} — {response.text}")
        raise Exception(
            f"Graph API Error {response.status_code}: {response.text}"
        )

    azure_users: List[Dict[str, Any]] = response.json().get("value", [])
    results = []

    if db is not None and azure_users:
        try:
            for gu in azure_users:
                local_id = _jit_upsert_user(db, gu)
                if local_id:
                    results.append({
                        "id": local_id,
                        "o365_id": gu.get("id"),
                        "displayName": gu.get("displayName"),
                        "mail": gu.get("mail")
                    })
            db.commit()
        except Exception as exc:
            db.rollback()
            print(f"[JIT SYNC WARNING] Batch commit failed, rolled back: {exc}")
            # Fallback to returning raw azure users if sync fails completely
            return azure_users

    return results if results else azure_users

def sync_all_users_task():
    import logging
    from app.core.database import SessionLocal
    logger = logging.getLogger("app.sync")
    logger.info("Starting manual Azure AD users sync.")
    try:
        token = get_graph_token()
    except Exception as e:
        logger.error(f"Error acquiring Graph token for sync: {e}")
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "ConsistencyLevel": "eventual",
    }

    url = f"{settings.MS_GRAPH_BASE_URL}/v1.0/users?$select=id,displayName,mail,userPrincipalName"
    total_synced = 0

    with SessionLocal() as db:
        while url:
            logger.info(f"Syncing batch from MS Graph...")
            resp = get(url, headers=headers)
            if resp.status_code != 200:
                logger.error(f"Graph API Error during sync: {resp.status_code} {resp.text}")
                break
            
            data = resp.json()
            users = data.get("value", [])
            for gu in users:
                try:
                    local_id = _jit_upsert_user(db, gu)
                    if local_id:
                        total_synced += 1
                    db.commit()
                except Exception as e:
                    db.rollback()
                    logger.warning(f"Failed to sync user {gu.get('mail')}: {e}")
            url = data.get("@odata.nextLink")
            
    logger.info(f"Azure AD sync complete. Total users synced/checked: {total_synced}")
