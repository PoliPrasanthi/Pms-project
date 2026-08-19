import httpx

PMS_PROJECTS_URL = "http://127.0.0.1:8000/api/v1/projects/"
PMS_TASKS_URL = "http://127.0.0.1:8000/api/v1/tasks/"


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

    projects = []

    for project in data.get("items", []):

        projects.append({
            "project_id": project.get("public_id"),
            "project_name": project.get("project_name"),
            "status": project.get("project_status_external"),
            "customer_name": project.get("customer_name"),
            "client_name": project.get("client_name"),
            "billing_model": project.get("billing_model"),
            "project_type": project.get("project_type"),
            "expected_start_date": project.get("expected_start_date"),
            "expected_end_date": project.get("expected_end_date"),
            "estimated_hours": project.get("estimated_hours"),
            "actual_hours": project.get("actual_hours"),
            "owner": (
                project.get("owner", {}).get("display_name")
                if project.get("owner")
                else None
            ),
            "project_manager": (
                project.get("project_manager", {}).get("display_name")
                if project.get("project_manager")
                else None
            ),
            "team_members": [
                member.get("user", {}).get("display_name")
                for member in project.get("team_members", [])
                if member.get("user")
            ],
            "completion_percentage": project.get(
                "completion_percentage"
            ),
            "task_count": project.get("task_count", 0),
            "issue_count": project.get("issue_count", 0),
            "milestone_count": project.get(
                "milestone_count", 0
            ),
        })

    completed_projects = [
        project
        for project in projects
        if project.get("status") == "Completed"
    ]

    ongoing_projects = [
        project
        for project in projects
        if project.get("status") in (
            "In Progress",
            "Ongoing",
        )
    ]

    pending_projects = [
        project
        for project in projects
        if project.get("status") == "Pending"
    ]

    status_counts = {}

    for project in projects:

        status = project.get("status")

        if status:
            status_counts[status] = (
                status_counts.get(status, 0) + 1
            )

    return {
        "total": len(projects),

        "status_counts": status_counts,

        "completed_count": len(completed_projects),
        "ongoing_count": len(ongoing_projects),
        "pending_count": len(pending_projects),

        "completed_projects": [
            {
                "project_name": project.get("project_name"),
                "status": project.get("status"),
            }
            for project in completed_projects
        ],

        "ongoing_projects": [
            {
                "project_name": project.get("project_name"),
                "status": project.get("status"),
            }
            for project in ongoing_projects
        ],

        "pending_projects": [
            {
                "project_name": project.get("project_name"),
                "status": project.get("status"),
            }
            for project in pending_projects
        ],
        "projects": projects,
    }
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

        data = response.json()

    tasks = []

    for task in data.get("items", []):

        project = task.get("project") or {}
        status_master = task.get("status_master") or {}
        priority_master = task.get("priority_master") or {}
        assignee = task.get("assignee") or {}

        tasks.append({
            "task_id": task.get("id"),
            "public_id": task.get("public_id"),
            "task_name": task.get("task_name"),
            "description": task.get("description"),

            "project_name": project.get("project_name"),

            "status": (
                status_master.get("label")
                or status_master.get("value")
            ),

            "priority": (
                priority_master.get("label")
                or priority_master.get("value")
            ),

            "assignee": assignee.get("name"),

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
        })

    return {
        "total": data.get("total", 0),
        "status_counts": data.get(
            "status_counts",
            {}
        ),
        "tasks": tasks,
    }