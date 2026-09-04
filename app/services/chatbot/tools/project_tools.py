from datetime import datetime, timedelta, timezone

import httpx


PMS_BASE_URL = "http://127.0.0.1:8000/api/v1/"

PMS_MODULE_URL = {
    "projects": "projects/",
    "tasks": "tasks/",
    "tasklists": "tasklists/",
    "issues": "issues/",
    "milestones": "milestones/",
    "timelogs": "timelogs/",
    "permissions": "chatbot/permissions",
}


REQUIRED_TASK_FIELDS = [
    "task_name",
    "project_id",

    "due_date",
]

REQUIRED_PROJECT_FIELDS = [
    "account_name",
    "project_name",
    "customer_name",
    "project_id_sync",
    "billing_model",
    "project_type",
    "status_id",
    "priority_id",
    "expected_start_date",
    "expected_end_date",
    "delivery_head_id",
    "user_emails"
]

DEFAULT_TASK_VALUES = {
    "priority_id": "22",
    "status_id": "16",
    "start_date": datetime.now(timezone.utc).date().isoformat(),
}
DEFAULT_PROJECT_VALUES = {
    "status_id": "9",
    "priority_id": "22",
    "expected_start_date": datetime.now(timezone.utc).date().isoformat(),
    "project_type":"42",
    "billing_model":"43"
}
    



async def get_my_projects(
    access_token: str,
    arguments: dict | None = None,
):
    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            PMS_BASE_URL + PMS_MODULE_URL["projects"],
            headers=headers,
            timeout=60.0,
        )

        response.raise_for_status()

        return response.json()


async def get_my_tasks(
    access_token: str,
    arguments: dict | None = None,
):
    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            PMS_BASE_URL + PMS_MODULE_URL["tasks"],
            headers=headers,
            timeout=60.0,
        )

        response.raise_for_status()

        return response.json()


async def get_my_tasklists(
    access_token: str,
    arguments: dict | None = None,
):
    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            PMS_BASE_URL + PMS_MODULE_URL["tasklists"],
            headers=headers,
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

    payload["owner_id"] = current_user.id

    async with httpx.AsyncClient(
        timeout=60.0
    ) as client:
        print("Payload for create_task:", payload)
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

    payload["project_manager_id"] = current_user.id
    print("Payload for create_project:", payload)
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