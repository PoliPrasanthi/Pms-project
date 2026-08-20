import httpx

PMS_PROJECTS_URL = "http://127.0.0.1:8000/api/v1/projects/"
PMS_TASKS_URL = "http://127.0.0.1:8000/api/v1/tasks/"
PMS_ISSUES_URL = "http://127.0.0.1:8000/api/v1/issues/"
PMS_TASKLISTS_URL = "http://127.0.0.1:8000/api/v1/tasklists/"



async def get_my_projects(access_token: str):

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

        data = response.json()

    return data
async def get_my_tasks(access_token: str):

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

async def get_my_tasklists(access_token: str):

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
async def get_my_issues(access_token: str):
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