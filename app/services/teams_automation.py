from __future__ import annotations

from logging import getLogger
from typing import List, Optional

logger = getLogger("app.teams_automation")

def create_ms_team_for_project(
    project_name: str,
    member_emails: List[str],
    project_id: Optional[int] = None,
) -> Optional[str]:
    from httpx import Client
    from app.core.config import settings

    tenant_id     = settings.AZURE_TENANT_ID
    client_id     = settings.AZURE_CLIENT_ID
    client_secret = settings.AZURE_CLIENT_SECRET

    if not all([tenant_id, client_id, client_secret]):
        logger.warning(
            "MS Teams automation skipped for project '%s' — Azure credentials not configured.",
            project_name,
        )
        return None

    try:
        with Client(timeout=30.0) as client:
            
            token_resp = client.post(
                f"{settings.MS_LOGIN_BASE_URL}/{tenant_id}/oauth2/v2.0/token",
                data={
                    "grant_type":    "client_credentials",
                    "client_id":     client_id,
                    "client_secret": client_secret,
                    "scope":         f"{settings.MS_GRAPH_BASE_URL}/.default",
                },
            )
            token_resp.raise_for_status()
            access_token = token_resp.json()["access_token"]

            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type":  "application/json",
            }

            create_resp = client.post(
                f"{settings.MS_GRAPH_BASE_URL}/v1.0/teams",
                headers=headers,
                json={
                    "template@odata.bind": f"{settings.MS_GRAPH_BASE_URL}/v1.0/teamsTemplates('standard')",
                    "displayName":         project_name[:256],
                    "description":         f"{settings.PROJECT_NAME} — {project_name} (Project ID: {project_id})",
                    "memberSettings": {"allowCreateUpdateChannels": True},
                    "messagingSettings": {"allowUserEditMessages": True, "allowUserDeleteMessages": True},
                    "funSettings": {"allowGiphy": False},
                },
            )
            
            if create_resp.status_code not in (201, 202):
                logger.error(
                    "Failed to create MS Team for project '%s': %s %s",
                    project_name, create_resp.status_code, create_resp.text,
                )
                return None

            team_id = create_resp.headers.get("location", "").split("/teams('")[-1].rstrip("')")
            if not team_id:
                logger.warning("MS Team created for '%s' but could not parse team_id from Location header.", project_name)
                return None

            logger.info("MS Team created: team_id=%s for project='%s'", team_id, project_name)

            for email in member_emails:
                try:
                    member_resp = client.post(
                        f"{settings.MS_GRAPH_BASE_URL}/v1.0/teams/{team_id}/members",
                        headers=headers,
                        json={
                            "@odata.type":    "#microsoft.graph.aadUserConversationMember",
                            "roles":          [],
                            "user@odata.bind": f"{settings.MS_GRAPH_BASE_URL}/v1.0/users('{email}')",
                        },
                    )
                    if member_resp.status_code not in (200, 201):
                        logger.warning("Could not add %s to team %s: %s", email, team_id, member_resp.text)
                except Exception as member_exc:
                    logger.warning("Exception adding %s to MS Team: %s", email, member_exc)

            logger.info("MS Teams automation complete for project='%s': %d members processed.", project_name, len(member_emails))
            return team_id

    except Exception as exc:
        logger.error(
            "MS Teams automation failed for project='%s' (project_id=%s): %s",
            project_name, project_id, exc,
            exc_info=True,
        )
        return None
