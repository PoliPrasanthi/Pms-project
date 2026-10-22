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

@router.get("/projects")
async def get_chatbot_projects(
    scope: str | None = None,
    project_name: str | None = None,
    public_id: str | None = None,
    person_name: str | None = None,
    db: AsyncSession = Depends(get_async_db),
    current_user=Depends(get_current_user),
):
    from app.core.security import get_user_view_level

    view_level = get_user_view_level(current_user, "proj-view")

    # --------------------------------------------------
    # 1. Fetch projects using existing permission logic
    # --------------------------------------------------
    all_projects = []
    skip = 0
    batch_size = 100

    service_user = (
        current_user
        if view_level not in ("All", None)
        else None
    )

    while True:
        result = await project_service.get_projects(
            db,
            skip=skip,
            limit=batch_size,
            include_all=True,
            current_user=service_user,
            view_level=view_level,
        )

        if isinstance(result, dict):
            batch = result.get("items", [])
            total = result.get("total")
        else:
            batch = result
            total = None

        if not batch:
            break

        all_projects.extend(batch)
        skip += len(batch)

        if total is not None and len(all_projects) >= total:
            break

        if len(batch) < batch_size:
            break

    # --------------------------------------------------
    # 2. Get authenticated user's ID
    # --------------------------------------------------
    current_user_id = getattr(current_user, "id", None)

    formatted_projects = []

    def ids_match(first, second):
        if first is None or second is None:
            return False

        return str(first) == str(second)

    normalized_scope = (scope or "").strip().lower()

    # --------------------------------------------------
    # 3. Filter and format projects
    # --------------------------------------------------
    for project in all_projects:
        manager = project.get("project_manager") or {}
        delivery_head = project.get("delivery_head") or {}
        team_members = project.get("team_members") or []

        # Check project manager.
        is_project_manager = (
            ids_match(manager.get("id"), current_user_id)
            or ids_match(manager.get("user_id"), current_user_id)
            or ids_match(
                project.get("project_manager_id"),
                current_user_id,
            )
        )

        # Check delivery head.
        is_delivery_head = (
            ids_match(delivery_head.get("id"), current_user_id)
            or ids_match(
                delivery_head.get("user_id"),
                current_user_id,
            )
            or ids_match(
                project.get("delivery_head_id"),
                current_user_id,
            )
        )

        # Check team membership.
        is_team_member = any(
            ids_match(
                (member.get("user") or {}).get("id"),
                current_user_id,
            )
            or ids_match(
                (member.get("user") or {}).get("user_id"),
                current_user_id,
            )
            or ids_match(
                member.get("user_id"),
                current_user_id,
            )
            for member in team_members
        )

        # ----------------------------------------------
        # 4. Apply "mine" scope
        # ----------------------------------------------
        if normalized_scope == "mine":
            is_associated_with_user = (
                is_project_manager
                or is_delivery_head
                or is_team_member
            )

            if not is_associated_with_user:
                continue

        # ----------------------------------------------
        # 5. Apply optional project-name filter
        # ----------------------------------------------
        if project_name:
            actual_name = str(
                project.get("project_name") or ""
            ).strip().lower()

            requested_name = project_name.strip().lower()

            if requested_name not in actual_name:
                continue

        # ----------------------------------------------
        # 6. Apply optional public-ID filter
        # ----------------------------------------------
        if public_id:
            actual_public_id = str(
                project.get("public_id") or ""
            ).strip().lower()

            if public_id.strip().lower() not in actual_public_id:
                continue

        # ----------------------------------------------
        # 7. Apply optional person-name filter
        # ----------------------------------------------
        if person_name:
            requested_person = person_name.strip().lower()

            associated_names = [
                manager.get("display_name") or manager.get("name"),
                delivery_head.get("display_name")
                or delivery_head.get("name"),
            ]

            for member in team_members:
                user = member.get("user") or {}

                associated_names.append(
                    user.get("display_name") or user.get("name")
                )

            name_matches = any(
                requested_person in str(name).lower()
                for name in associated_names
                if name
            )

            if not name_matches:
                continue

        # ----------------------------------------------
        # 8. Format project response
        # ----------------------------------------------
        status = project.get("status_master") or {}
        priority = project.get("priority_master") or {}

        # billing_model may be a plain string (e.g. "T&M") or a master-data
        # object/dict. Handle both shapes so project listing never fails with
        # AttributeError when the API already returns the display value.
        billing_model = project.get("billing_model")
        if isinstance(billing_model, dict):
            billing_type = (
                billing_model.get("value")
                or billing_model.get("display_name")
                or billing_model.get("label")
                or billing_model.get("name")
            )
        elif isinstance(billing_model, str):
            billing_type = billing_model
        elif billing_model is not None:
            billing_type = (
                getattr(billing_model, "value", None)
                or getattr(billing_model, "display_name", None)
                or getattr(billing_model, "label", None)
                or getattr(billing_model, "name", None)
            )
        else:
            billing_type = None

        formatted_team_members = [
            (member.get("user") or {}).get("display_name")
            or (member.get("user") or {}).get("name")
            for member in team_members
            if (
                (member.get("user") or {}).get("display_name")
                or (member.get("user") or {}).get("name")
            )
        ]

        formatted_projects.append({
            "public_id": project.get("public_id"),
            "project_name": project.get("project_name"),
            "id": project.get("id"),
            "manager_name": (
                manager.get("display_name")
                or manager.get("name")
            ),
            "delivery_head_name": (
                delivery_head.get("display_name")
                or delivery_head.get("name")
            ),
            "completion_percentage": project.get(
                "completion_percentage"
            ),
            "team_members": formatted_team_members,
            "project_status": (
                status.get("display_name")
                or status.get("label")
            ),
            "project_completion_percentage": project.get(
                "completion_percentage"
            ),
            "priority": (
                priority.get("display_name")
                or priority.get("label")
            ),
            "description": project.get("description"),
            "expected_start_date": project.get(
                "expected_start_date"
            ),
            "expected_end_date": project.get(
                "expected_end_date"
            ),
            "billing_type": billing_type,
        })

    # The formatter displays five projects in chat, so provide the exact
    # number of projects remaining from the same filtered result set.
    # This endpoint currently builds formatted_projects from all accessible
    # matching records, making its length the authoritative total for this query.
    total_project_count = len(formatted_projects)
    has_more_projects = total_project_count > 5

    print(
        f"[PROJECT DEBUG] User ID: {current_user_id} | "
        f"Scope: {normalized_scope or 'default'} | "
        f"Fetched: {len(all_projects)} | "
        f"Returned: {total_project_count} | "
        f"Total: {total_project_count} | "
        f"Has more: {has_more_projects}",
        flush=True,
    )

    return {
        "projects": formatted_projects,
        "total_count": total_project_count,
        "has_more": has_more_projects,
    }


@router.get("/tasks")
async def get_chatbot_tasks(
    scope: str | None = None,
    task_name: str | None = None,
    project_name: str | None = None,
    public_id: str | None = None,
    person_name: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user),
):
    """Return chatbot-ready tasks using the same permission-aware pattern as projects."""
    from app.core.security import get_user_view_level

    view_level = get_user_view_level(current_user, "task-view")
    service_user = current_user if view_level not in ("All", None) else None

    # Always retrieve the complete authorized candidate set. The chatbot may
    # display only five records initially, but display limits must never limit
    # the records available for post-tool filtering or View More.
    normalized_scope = (scope or "").strip().lower()
    requested_task = (task_name or "").strip().lower()
    requested_project = (project_name or "").strip().lower()
    requested_public_id = (public_id or "").strip().lower()
    requested_person = (person_name or "").strip().lower()
    requested_status = (status or "").strip().lower()
    requested_priority = (priority or "").strip().lower()

    all_tasks = []
    skip = 0
    batch_size = 100
    while True:
        result = await task_service.get_tasks(
            db,
            skip=skip,
            limit=batch_size,
            current_user=service_user,
            view_level=view_level or "O",
            return_raw=True,
        )
        if isinstance(result, dict):
            batch = result.get("items", []) or []
            total = result.get("total")
        else:
            batch = result or []
            total = None

        if not batch:
            break
        all_tasks.extend(batch)
        skip += len(batch)

        if total is not None and len(all_tasks) >= total:
            break
        if len(batch) < batch_size:
            break

    total_task_count = len(all_tasks)

    current_user_id = getattr(current_user, "id", None)

    def value(obj, key, default=None):
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    def display_name(user):
        if not user:
            return None
        first = value(user, "first_name", "") or ""
        last = value(user, "last_name", "") or ""
        name = f"{first} {last}".strip()
        return name or value(user, "display_name") or value(user, "name")

    def same_id(a, b):
        return a is not None and b is not None and str(a) == str(b)

    formatted_tasks = []
    for task in all_tasks:
        project = value(task, "project")
        task_list_obj = value(task, "task_list")
        milestone = value(task, "milestone")
        status_obj = value(task, "status_master")
        priority_obj = value(task, "priority_master")
        status_name = value(status_obj, "value") or value(status_obj, "label") or value(status_obj, "name")
        priority_name = value(priority_obj, "value") or value(priority_obj, "label") or value(priority_obj, "name")
        project_title = value(project, "project_name")
        task_title = value(task, "task_name") or ""
        task_public_id = value(task, "public_id") or ""

        primary_owner = value(task, "single_owner")
        if primary_owner is None:
            primary_owner = value(task, "owner")
        primary_assignee = value(task, "assignee")
        owners = list(value(task, "owners", []) or [])
        assignees = list(value(task, "assignees", []) or [])
        owner_names = list(dict.fromkeys(n for n in [display_name(primary_owner), *(display_name(u) for u in owners)] if n))
        assignee_names = list(dict.fromkeys(n for n in [display_name(primary_assignee), *(display_name(u) for u in assignees)] if n))

        # Explicit "mine" scope means ownership/assignment/creation by this user.
        if normalized_scope == "mine":
            user_is_owner = same_id(value(task, "owner_id"), current_user_id) or same_id(value(primary_owner, "id"), current_user_id) or any(same_id(value(u, "id"), current_user_id) for u in owners)
            user_is_assignee = same_id(value(task, "assignee_id"), current_user_id) or same_id(value(primary_assignee, "id"), current_user_id) or any(same_id(value(u, "id"), current_user_id) for u in assignees)
            user_created = same_id(value(task, "created_by_id"), current_user_id) or same_id(value(value(task, "creator"), "id"), current_user_id)
            if not (user_is_owner or user_is_assignee or user_created):
                continue

        if requested_task and requested_task not in task_title.lower():
            continue
        if requested_public_id and requested_public_id not in str(task_public_id).lower():
            continue
        if requested_project and requested_project not in str(project_title or "").lower():
            continue
        if requested_status and requested_status not in str(status_name or "").lower():
            continue
        if requested_priority and requested_priority not in str(priority_name or "").lower():
            continue
        if requested_person:
            people = owner_names + assignee_names
            creator_name = display_name(value(task, "creator"))
            if creator_name:
                people.append(creator_name)
            if not any(requested_person in name.lower() for name in people):
                continue

        formatted_tasks.append({
            "id": value(task, "id"),
            "public_id": task_public_id,
            "task_name": task_title,
            "description": value(task, "description"),
            "project_id": value(task, "project_id"),
            "project_name": project_title,
            "task_list_id": value(task, "task_list_id"),
            "task_list_name": value(task_list_obj, "name"),
            "milestone_id": value(task, "milestone_id"),
            "milestone_name": value(milestone, "milestone_name") or value(milestone, "name"),
            "owner_name": owner_names[0] if owner_names else None,
            "owners": owner_names,
            "assignee_name": assignee_names[0] if assignee_names else None,
            "assignees": assignee_names,
            "status": status_name,
            "priority": priority_name,
            "completion_percentage": value(task, "completion_percentage"),
            "start_date": str(value(task, "start_date")) if value(task, "start_date") is not None else None,
            "due_date": str(value(task, "due_date")) if value(task, "due_date") is not None else None,
            "estimated_hours": value(task, "estimated_hours"),
            "work_hours": value(task, "work_hours"),
        })

    print(
        f"[CHATBOT TASK DEBUG] User ID: {current_user_id} | Scope: {normalized_scope or 'default'} | Fetched: {len(all_tasks)} | Returned: {len(formatted_tasks)} | Total: {total_task_count}",
        flush=True,
    )
    # Return every matching authorized task to the graph. The chat formatter
    # controls the initial five-record display and View More navigation.
    response_payload = {
        "tasks": formatted_tasks,
        "total_count": len(formatted_tasks),
        "has_more": len(formatted_tasks) > 5,
    }
    return response_payload

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