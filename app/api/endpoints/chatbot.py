from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.endpoints import tasks
from app.models import task_list
from app.models.user import User
from app.core.database import get_async_db
from app.services import project_service, task_list_service, task_service
import httpx

from fastapi import Depends, HTTPException, Request

from app.core.security import get_current_user

from app.schemas.chatbot import (
    ChatRequest,
    ChatResponse,
)

from app.services.chatbot.agent import run_agent


router = APIRouter()


def get_access_token(
    request: Request,
) -> str:
    access_token = request.headers.get(
        "Authorization",
        "",
    )

    if access_token.startswith("Bearer "):
        access_token = access_token[7:]

    return access_token


@router.post(
    "/chat",
    response_model=ChatResponse,
)
async def chat(
    request: Request,
    chat_request: ChatRequest,
    current_user=Depends(get_current_user),
):
    access_token = get_access_token(request)

    return await run_agent(
        user_message=chat_request.message,
        access_token=access_token,
        user_id=current_user.id,
        current_user=current_user,
        action=chat_request.action,
        form_data=chat_request.form_data,
    )

@router.get("/permissions")
async def get_my_permissions(
    current_user=Depends(get_current_user),
):
    permissions = current_user.role.permissions or {}

    if isinstance(permissions, str):
        import json

        try:
            permissions = json.loads(permissions)
        except Exception:
            permissions = {}

    # Task List permissions are available to all users
    permissions["tasklist-edit"] = True
    permissions["tasklist-view"] = "A"
    permissions["tasklist-create"] = True
    permissions["tasklist-delete"] = True

    return {
        "permissions": permissions
    }

@router.get("/projects")
async def get_chatbot_projects(
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(get_current_user),
):
    from app.core.security import get_user_view_level

    view_level = get_user_view_level(current_user, "proj-view")

    result = await project_service.get_projects(
        db,
        skip=0,
        limit=100,
        include_all=True,
        current_user=current_user if view_level not in ("All", None) else None,
        view_level=view_level,
    )

    projects = result.get("items", []) if isinstance(result, dict) else result

    current_user_id = getattr(current_user, "id", None)
    formatted_projects = []

    for project in projects:
        manager = project.get("project_manager") or {}
        delivery_head = project.get("delivery_head") or {}
        team_members = project.get("team_members") or []

        # Check current user's project role
        is_project_manager = (
            manager.get("id") == current_user_id
            or manager.get("user_id") == current_user_id
            or project.get("project_manager_id") == current_user_id
        )

        is_delivery_head = (
            delivery_head.get("id") == current_user_id
            or delivery_head.get("user_id") == current_user_id
            or project.get("delivery_head_id") == current_user_id
        )

        is_team_member = any(
            (
                member.get("user") or {}
            ).get("id") == current_user_id
            or (
                member.get("user") or {}
            ).get("user_id") == current_user_id
            or member.get("user_id") == current_user_id
            for member in team_members
        )

        # Include only projects related to current user
        if not (is_project_manager or is_delivery_head or is_team_member):
            continue

        status = project.get("status_master") or {}
        priority = project.get("priority_master") or {}

        formatted_team_members = [
            (member.get("user") or {}).get("display_name")
            or (member.get("user") or {}).get("name")
            for member in team_members
            if (member.get("user") or {}).get("display_name")
            or (member.get("user") or {}).get("name")
        ]

        formatted_projects.append({
            "public_id": project.get("public_id"),
            "project_name": project.get("project_name"),
            "id": project.get("id"),
            "manager_name": manager.get("display_name") or manager.get("name"),
            "delivery_head_name": (
                delivery_head.get("display_name")
                or delivery_head.get("name")
            ),
            "completion_percentage": project.get("completion_percentage"),
            "team_members": formatted_team_members,
            "project_status": status.get("value") or status.get("label"),
            "priority": priority.get("value") or priority.get("label"),
            "description": project.get("description"),
            "expected_start_date": project.get("expected_start_date"),
            "expected_end_date": project.get("expected_end_date"),
            "billing_type": project.get("billing_model"),
        })

    print("Projects for current user:", formatted_projects)

    return {"projects": formatted_projects}


@router.get("/tasklists")
async def get_chatbot_tasklists(
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(get_current_user),
):
    result = await task_list_service.get_task_lists(
        db,
        skip=0,
        limit=100,
        project_id=None,
        current_user=current_user,
        view_level="O",
    )

    tasklists = (
        result.get("items", [])
        if isinstance(result, dict)
        else result
    )

    formatted_tasklists = []

    for tasklist in tasklists:
        project = tasklist.project

        formatted_tasklists.append({
            "id": tasklist.id,
            "name": tasklist.name,
            "description": tasklist.description,
            "project_name": project.project_name if project else None,
        })

    return {"tasklists": formatted_tasklists}