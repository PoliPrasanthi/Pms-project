import httpx


PMS_PROJECTS_URL = "http://127.0.0.1:8000/api/v1/projects/"
PMS_TASKS_URL = "http://127.0.0.1:8000/api/v1/tasks/"
PMS_ISSUES_URL = "http://127.0.0.1:8000/api/v1/issues/"
PMS_TASKLISTS_URL = "http://127.0.0.1:8000/api/v1/tasklists/"
PMS_PROJECT_AUTH_URL = "http://127.0.0.1:8000/api/v1/projects/can-create"
PMS_MILESTONES_URL = "http://127.0.0.1:8000/api/v1/milestones/"
PMS_TIMELOGS_URL = "http://127.0.0.1:8000/api/v1/timelogs/"


async def get_my_projects(access_token: str,arguments: dict = None):

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    async with httpx.AsyncClient() as client:

        response = await client.get(
            PMS_PROJECTS_URL,
            headers=headers,
            timeout=60.0,
        )

        response.raise_for_status()

        return response.json()


async def get_my_tasks(access_token: str,arguments: dict = None):

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    async with httpx.AsyncClient() as client:

        response = await client.get(
            PMS_TASKS_URL,
            headers=headers,
            timeout=60.0,
        )

        response.raise_for_status()

        return response.json()


async def get_my_tasklists(access_token: str,arguments: dict = None):

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    async with httpx.AsyncClient() as client:

        response = await client.get(
            PMS_TASKLISTS_URL,
            headers=headers,
            timeout=60.0,
        )

        response.raise_for_status()

        return response.json()


async def get_my_issues(access_token: str,arguments: dict = None):

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    async with httpx.AsyncClient() as client:

        response = await client.get(
            PMS_ISSUES_URL,
            headers=headers,
            timeout=60.0,
        )

        response.raise_for_status()

        return response.json()

async def check_create_project_permission(access_token: str,arguments: dict = None):
    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    async with httpx.AsyncClient(
        timeout=30.0
    ) as client:

        response = await client.get(
            PMS_PROJECT_AUTH_URL,
            headers=headers
        )

    if response.status_code == 200:
        return {
            "allowed": True
        }

    if response.status_code == 403:
        return {
            "allowed": False,
            "reason": "You do not have permission to create a project."
        }

    if response.status_code == 401:
        return {
            "allowed": False,
            "reason": "Authentication failed."
        }

    return {
        "allowed": False,
        "reason": response.text,
        "status_code": response.status_code
    }


async def get_my_milestones(access_token: str,arguments: dict = None):
    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    async with httpx.AsyncClient() as client:

        response = await client.get(
            PMS_MILESTONES_URL,
            headers=headers,
            timeout=60.0,
        )

        response.raise_for_status()

        return response.json()
    
    
async def get_my_timelogs(access_token: str,arguments: dict = None):
    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    async with httpx.AsyncClient() as client:

        response = await client.get(
            PMS_TIMELOGS_URL,
            headers=headers,
            timeout=60.0,
        )

        response.raise_for_status()

        return response.json()