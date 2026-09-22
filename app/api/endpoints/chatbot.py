from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.user import User
from app.core.database import get_async_db
from app.services import project_service, task_service

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

    view_level = get_user_view_level(
        current_user,
        "proj-view",
    )

    result = await project_service.get_projects(
        db,
        skip=0,
        limit=100,
        include_all=True,
        current_user=(
            current_user
            if view_level not in ("All", None)
            else None
        ),
        view_level=view_level,
    )

    projects = (
        result.get("items", [])
        if isinstance(result, dict)
        else result
    )

    current_user_id = getattr(
        current_user,
        "id",
        None,
    )

    formatted_projects = []

    for project in projects:

        manager = (
            project.get("project_manager")
            or {}
        )

        delivery_head = (
            project.get("delivery_head")
            or {}
        )

        team_members = (
            project.get("team_members")
            or []
        )

        # Check project manager
        is_project_manager = (
            manager.get("id") == current_user_id
            or manager.get("user_id") == current_user_id
            or project.get("project_manager_id") == current_user_id
        )

        # Check delivery head
        is_delivery_head = (
            delivery_head.get("id") == current_user_id
            or delivery_head.get("user_id") == current_user_id
            or project.get("delivery_head_id") == current_user_id
        )

        # Check team member
        is_team_member = False

        for member in team_members:

            user = member.get("user") or {}

            member_user_id = (
                user.get("id")
                or user.get("user_id")
                or member.get("user_id")
            )

            if member_user_id == current_user_id:
                is_team_member = True
                break

        # Include only projects where current user
        # is manager, delivery head, or team member.
        if not (
            is_project_manager
            or is_delivery_head
            or is_team_member
        ):
            continue

        status = (
            project.get("status_master")
            or {}
        )

        priority = (
            project.get("priority_master")
            or {}
        )

        formatted_team_members = []

        for member in team_members:

            user = member.get("user") or {}

            name = (
                user.get("display_name")
                or user.get("name")
            )

            if name:
                formatted_team_members.append(name)

        formatted_projects.append({
            "public_id": project.get(
                "public_id"
            ),
            "project_name": project.get(
                "project_name"
            ),
            "manager_name": (
                manager.get("display_name")
                or manager.get("name")
            ),
            "delivery_head_name": (
                delivery_head.get("display_name")
                or delivery_head.get("name")
            ),
            "team_members": formatted_team_members,
            "project_status": (
                status.get("value")
                or status.get("label")
            ),
            "priority": (
                priority.get("value")
                or priority.get("label")
            ),
            "description": project.get(
                "description"
            ),
            "expected_start_date": project.get(
                "expected_start_date"
            ),
            "expected_end_date": project.get(
                "expected_end_date"
            ),
            "billing_type": project.get(
                "billing_model"
            ),
        })

    print(
        "Projects for current user:",
        formatted_projects,
    )

    return {
        "projects": formatted_projects
    }




@router.get("/tasks")
async def get_chatbot_tasks(
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(get_current_user),
):
    from app.core.security import get_user_view_level

    view_level = get_user_view_level(
        current_user,
        "task-view",
    )

    result = await task_service.get_tasks(
        db,
        skip=0,
        limit=100,
        current_user=(
            current_user
            if view_level not in ("All", None)
            else None
        ),
        view_level=view_level,
    )

    tasks = (
        result.get("items", [])
        if isinstance(result, dict)
        else result
    )

    # ============================================================
    # FILTER ONLY TASKS ASSIGNED TO CURRENT USER
    # ============================================================

    current_user_id = getattr(
        current_user,
        "id",
        None,
    )

    filtered_tasks = []

    for task in tasks:
        assignees = task.get("assignees") or []

        is_assigned_to_current_user = any(
            (
                user.get("id") == current_user_id
                or user.get("user_id") == current_user_id
            )
            for user in assignees
            if isinstance(user, dict)
        )

        if is_assigned_to_current_user:
            filtered_tasks.append(task)

    # Use only tasks assigned to current user
    tasks = filtered_tasks

    # ============================================================
    # FORMAT TASKS
    # ============================================================

    formatted_tasks = []

    for task in tasks:

        project = task.get("project") or {}
        status = task.get("status_master") or {}
        priority = task.get("priority_master") or {}

        owners = []

        for user in task.get("owners") or []:
            name = (
                user.get("display_name")
                or user.get("name")
            )

            if name:
                owners.append(name)

        assignees = []

        for user in task.get("assignees") or []:
            name = (
                user.get("display_name")
                or user.get("name")
            )

            if name:
                assignees.append(name)

        formatted_tasks.append({
            "public_id": task.get("public_id"),
            "task_name": task.get("task_name"),
            "project_name": project.get("project_name"),
            "status": (
                status.get("value")
                or status.get("label")
            ),
            "priority": (
                priority.get("value")
                or priority.get("label")
            ),
            "owners": owners,
            "assignees": assignees,
            "start_date": task.get("start_date"),
            "due_date": task.get("due_date"),
            "completion_percentage": task.get(
                "completion_percentage"
            ),
            "estimated_hours": task.get(
                "estimated_hours"
            ),
            "work_hours": task.get(
                "work_hours"
            ),
            "billing_type": task.get(
                "billing_type"
            ),
            "description": task.get(
                "description"
            ),
        })

    return {
        "tasks": formatted_tasks
    }
@router.get("/chatbot-user")
async def get_chatbot_user(
    current_user: User = Depends(get_current_user),
):
    return {
        "id": current_user.id,
        "name": (
            f"{current_user.first_name or ''} "
            f"{current_user.last_name or ''}"
        ).strip(),
        "role": current_user.role.name
        if current_user.role
        else None,
    }