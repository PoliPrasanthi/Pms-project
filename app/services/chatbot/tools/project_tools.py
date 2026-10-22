from datetime import datetime, timedelta, timezone
import uuid

import httpx


PMS_BASE_URL = "http://127.0.0.1:8000/api/v1/"

PMS_MODULE_URL = {
    "projects_details": "chatbot/projects",
    "projects": "projects/",
    "task_details": "chatbot/tasks",
    "tasks": "tasks/",
    "tasklists": "tasklists/",
    "tasklists_details": "chatbot/tasklists",
    "issues": "issues/",
    "milestones": "milestones/",
    "timelogs": "timelogs/",
    "permissions": "chatbot/permissions",
    "chatbot_user": "chatbot/chatbot-user",
}


REQUIRED_TASK_FIELDS = [
    "task_name",
    "project_id",
    "due_date",
    "estimated_hours",
    "assignee_emails",
    "owner_emails",
    "priority_id",
    "status_id",
    "start_date",

]
REQUIRED_TASKLIST_FIELDS = [
    "name",
    "project_id",
]
REQUIRED_PROJECT_FIELDS = [
    "account_name",
    "project_name",
    "customer_name",
    "billing_model",
    "project_type",
    "project_id_sync",
    "project_manager_id",
    "status_id",
    "priority_id",
    "expected_start_date",
    "expected_end_date",
    "delivery_head_id",
    "user_emails"
]

REQUIRED_ISSUE_FIELDS = [
    "bug_name",
    "project_id",
    "assignee_id",
    "start_date",
    "due_date",
    "status_id",
    "severity_id",
]

REQUIRED_MILESTONE_FIELDS = [
    "milestone_name",
    "project_id"
]
DEFAULT_TASK_VALUES = {
    "priority_id": "22",
    "status_id": "16",
    "start_date": datetime.now(timezone.utc).date().isoformat(),
}
DEFAULT_PROJECT_VALUES = {
    "status_id": "9",
    "priority_id": "22",
    "project_id_sync":str(uuid.uuid4()),
    "expected_start_date": datetime.now(timezone.utc).date().isoformat(),
    "project_type":"42",
    "billing_model":"43"
}
DEFAULT_ISSUE_VALUES = {
    "status_id":"1",
    "severity_id":"26",
    "start_date": datetime.now(timezone.utc).date().isoformat(),
}

async def get_current_user_details(
    access_token: str,
    arguments: dict | None = None,
):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(
        timeout=30.0
    ) as client:

        response = await client.get(
            PMS_BASE_URL + PMS_MODULE_URL["chatbot_user"],
            headers=headers,
        )

        response.raise_for_status()

        return response.json()

async def get_my_permissions(
    access_token: str,
    arguments: dict | None = None,
):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(
        timeout=30.0
    ) as client:

        response = await client.get(
            PMS_BASE_URL + PMS_MODULE_URL["permissions"],
            headers=headers,
        )

        response.raise_for_status()

        return response.json()
    
import time
import httpx
import logging

logger = logging.getLogger(__name__)


# Read filters accepted by the chatbot's read endpoints. These are query filters,
# not permissions: the PMS backend must still enforce the authenticated user's access.
READ_QUERY_KEYS = {
    "scope", "project_name", "public_id", "person_name", "project_id",
    "task_name", "tasklist_name", "issue_name", "milestone_name",
    "status", "priority", "severity", "assignee_name", "owner_name",
    "start_date", "end_date", "skip", "limit",
}

def _read_query_params(arguments: dict | None) -> dict:
    if not isinstance(arguments, dict):
        return {}
    return {
        key: value.strip() if isinstance(value, str) else value
        for key, value in arguments.items()
        if key in READ_QUERY_KEYS and value is not None and value != ""
    }


def _find_project_pagination_metadata(payload: dict) -> tuple[int | None, bool | None]:
    """Find authoritative project pagination metadata across common API wrappers."""
    total_keys = (
        "total_count", "totalCount", "total", "total_projects", "totalProjects",
        "projects_count", "projectsCount", "total_records", "totalRecords",
    )
    more_keys = ("has_more", "hasMore", "has_next", "hasNext", "next_page", "nextPage")

    total = None
    has_more = None
    containers = [payload]
    # Search common nested response/pagination wrappers without changing records.
    for key in ("meta", "metadata", "pagination", "page_info", "pageInfo", "data"):
        child = payload.get(key)
        if isinstance(child, dict):
            containers.append(child)
            for nested_key in ("meta", "metadata", "pagination", "page_info", "pageInfo"):
                nested = child.get(nested_key)
                if isinstance(nested, dict):
                    containers.append(nested)

    for container in containers:
        if total is None:
            for key in total_keys:
                value = container.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    total = value
                    break
                if isinstance(value, str) and value.strip().isdigit():
                    total = int(value.strip())
                    break
        if has_more is None:
            for key in more_keys:
                value = container.get(key)
                if isinstance(value, bool):
                    has_more = value
                    break
                if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
                    has_more = value.strip().lower() == "true"
                    break
        if total is not None and has_more is not None:
            break

    return total, has_more


async def get_my_projects(
    access_token: str,
    arguments: dict | None = None,
):
    """Fetch the first page of projects and normalize pagination metadata for View More."""
    url = PMS_BASE_URL + PMS_MODULE_URL["projects_details"]
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }

    query_params = _read_query_params(arguments)
    query_params.setdefault("skip", 0)
    query_params.setdefault("limit", 5)

    start_time = time.perf_counter()
    logger.info(
        "[PROJECTS] Request started: url=%s, skip=%s, limit=%s",
        url, query_params.get("skip"), query_params.get("limit"),
    )
    timeout = httpx.Timeout(connect=15.0, read=120.0, write=15.0, pool=15.0)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, headers=headers, params=query_params)
            elapsed = time.perf_counter() - start_time
            logger.info(
                "[PROJECTS] Response received: status=%s, elapsed=%.2fs",
                response.status_code, elapsed,
            )
            response.raise_for_status()
            result = response.json()

            if isinstance(result, dict):
                total, has_more = _find_project_pagination_metadata(result)
                # The formatter expects these canonical keys. Only add them when
                # the API actually supplied equivalent authoritative metadata.
                if total is not None:
                    result["total_count"] = total
                if has_more is not None:
                    result["has_more"] = has_more

                records = next((result.get(key) for key in
                                ("items", "results", "projects", "data", "records")
                                if isinstance(result.get(key), list)), [])
                logger.info(
                    "[PROJECTS] Pagination metadata normalized: total_count=%s, "
                    "has_more=%s, returned_records=%s, metadata_keys=%s",
                    result.get("total_count"), result.get("has_more"), len(records),
                    list(result.keys()),
                )
                if total is None and has_more is None:
                    logger.warning(
                        "[PROJECTS] API response has no recognized total/has-more metadata. "
                        "The chatbot cannot show an accurate remaining-project count until "
                        "the projects endpoint returns a total count."
                    )
            return result

    except httpx.TimeoutException:
        logger.exception("[PROJECTS] Request timed out after %.2f seconds", time.perf_counter() - start_time)
        raise
    except httpx.HTTPStatusError:
        logger.exception("[PROJECTS] API returned an HTTP error")
        raise
    except httpx.RequestError:
        logger.exception("[PROJECTS] Network error while requesting projects")
        raise


async def get_my_tasks(
    access_token: str,
    arguments: dict | None = None,
):
    """Fetch tasks with an explicit read timeout and useful diagnostics."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    url = PMS_BASE_URL + PMS_MODULE_URL["task_details"]
    # List answers only need the first five rows; the endpoint returns the
    # authoritative total_count/has_more metadata for the View More link.
    query_params = _read_query_params(arguments)
    query_params.setdefault("skip", 0)
    query_params.setdefault("limit", 5)
    timeout = httpx.Timeout(
        connect=15.0,
        read=120.0,
        write=15.0,
        pool=15.0,
    )

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                url,
                headers=headers,
                params=query_params,
            )
            response.raise_for_status()
            return response.json()
    except httpx.TimeoutException:
        logger.exception("[TASKS] Request timed out while calling %s", url)
        raise
    except httpx.HTTPStatusError:
        logger.exception("[TASKS] PMS returned an HTTP error for %s", url)
        raise
    except httpx.RequestError:
        logger.exception("[TASKS] Network error while calling %s", url)
        raise


async def get_my_tasklists(
    access_token: str,
    arguments: dict | None = None,
):
    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            PMS_BASE_URL + PMS_MODULE_URL["tasklists_details"],
            headers=headers,
            params=_read_query_params(arguments),
            timeout=60.0,
        )

        response.raise_for_status()

        return response.json()


async def get_my_issues(
    access_token: str,
    arguments: dict | None = None,
):
    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            PMS_BASE_URL + PMS_MODULE_URL["issues"],
            headers=headers,
            params=_read_query_params(arguments),
            timeout=60.0,
        )

        response.raise_for_status()

        return response.json()


async def get_my_milestones(
    access_token: str,
    arguments: dict | None = None,
):
    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            PMS_BASE_URL + PMS_MODULE_URL["milestones"],
            headers=headers,
            params=_read_query_params(arguments),
            timeout=60.0,
        )

        response.raise_for_status()

        return response.json()


async def get_my_timelogs(
    access_token: str,
    arguments: dict | None = None,
):
    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            PMS_BASE_URL + PMS_MODULE_URL["timelogs"],
            headers=headers,
            params=_read_query_params(arguments),
            timeout=60.0,
        )

        response.raise_for_status()

        return response.json()

async def create_task(
    access_token: str,
    current_user,
    arguments: dict,
):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    payload = {
        key: value
        for key, value in arguments.items()
        if value is not None
    }

    # if payload.get("assignees") is not None:
    #     payload["owners"] = payload["assignees"]

    async with httpx.AsyncClient(
        timeout=60.0
    ) as client:

        response = await client.post(
            PMS_BASE_URL + PMS_MODULE_URL["tasks"],
            headers=headers,
            json=payload,
        )

        if response.status_code >= 400:
            return {
                "success": False,
                "status_code": response.status_code,
                "error": response.text,
            }

        return {
            "success": True,
            "status_code": response.status_code,
            "data": response.json(),
        }
async def create_project(
    access_token: str,
    current_user,
    arguments: dict,
):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    payload = {
        key: value
        for key, value in arguments.items()
        if value is not None
    }

    # payload["project_manager_id"] = current_user.id
    
    async with httpx.AsyncClient(
        timeout=60.0
    ) as client:

        response = await client.post(
            PMS_BASE_URL + PMS_MODULE_URL["projects"],
            headers=headers,
            json=payload,
        )

        if response.status_code >= 400:
            return {
                "success": False,
                "status_code": response.status_code,
                "error": response.text,
            }

        return {
            "success": True,
            "status_code": response.status_code,
            "data": response.json(),
        }

async def create_issue(
    access_token,
    current_user,
    arguments,
):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    payload = {
        key: value
        for key, value in arguments.items()
        if value is not None
    }

    payload["reporter_id"] = current_user.id

    async with httpx.AsyncClient(timeout=60.0) as client:

        response = await client.post(
            PMS_BASE_URL + PMS_MODULE_URL["issues"],
            headers=headers,
            json=payload,
        )

        if response.status_code >= 400:
            return {
                "success": False,
                "status_code": response.status_code,
                "error": response.text,
            }

        return {
            "success": True,
            "status_code": response.status_code,
            "data": response.json(),
        }

async def create_tasklist(
    access_token,
    current_user,
    arguments,
):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    payload = {
        key: value
        for key, value in arguments.items()
        if value is not None
    }

    async with httpx.AsyncClient(timeout=60.0) as client:

        response = await client.post(
            PMS_BASE_URL + PMS_MODULE_URL["tasklists"],
            headers=headers,
            json=payload,
        )

        if response.status_code >= 400:
            return {
                "success": False,
                "status_code": response.status_code,
                "error": response.text,
            }

        return {
            "success": True,
            "status_code": response.status_code,
            "data": response.json(),
        }

async def create_milestone(
    access_token: str,
    current_user,
    arguments: dict,
):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    payload = {
        key: value
        for key, value in arguments.items()
        if value is not None
    }

    async with httpx.AsyncClient(timeout=60.0) as client:

        response = await client.post(
            PMS_BASE_URL + PMS_MODULE_URL["milestones"],
            headers=headers,
            json=payload,
        )

        if response.status_code >= 400:
            return {
                "success": False,
                "status_code": response.status_code,
                "error": response.text,
            }

        return {
            "success": True,
            "status_code": response.status_code,
            "data": response.json(),
        }