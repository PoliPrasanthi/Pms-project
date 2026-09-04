from .project_tools import (
    get_my_projects,
    get_my_tasks,
    get_my_tasklists,
    get_my_issues,
    get_my_milestones,
    get_my_timelogs,
    create_task,
    create_project,
)


PROJECT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_my_projects",
            "description": (
                "Get the projects assigned to the "
                "currently authenticated user."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_my_tasks",
            "description": (
                "Get the tasks assigned to the "
                "currently authenticated user."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_my_tasklists",
            "description": (
                "Get the task lists available to "
                "the currently authenticated user."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_my_issues",
            "description": (
                "Get the issues associated with the "
                "currently authenticated user's projects."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_my_milestones",
            "description": (
                "Get the milestones accessible to "
                "the currently authenticated user."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_my_timelogs",
            "description": (
                "Get the time logs accessible to the "
                "currently authenticated user."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_creation",
            "description": (
                "Start a creation workflow for the requested PMS entity. "
                "Use entity_type='project' for project creation and "
                "entity_type='task' for task creation. "
                "Extract only values explicitly provided by the user. "
                "Do not create anything."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_type": {
                        "type": "string",
                        "enum": [
                            "task",
                            "project",
                            "issue",
                            "milestone",
                            "tasklist",
                        ],
                    },
                    "arguments": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                },
                "required": [
                    "entity_type",
                    "arguments",
                ],
            },
        },
    },
]


READ_TOOLS = {
    "get_my_projects",
    "get_my_tasks",
    "get_my_tasklists",
    "get_my_issues",
    "get_my_milestones",
    "get_my_timelogs",
}

CREATION_INTENT_TOOLS = {
    "start_creation",
}


TOOL_FUNCTIONS = {
    "get_my_projects": get_my_projects,
    "get_my_tasks": get_my_tasks,
    "get_my_tasklists": get_my_tasklists,
    "get_my_issues": get_my_issues,
    "get_my_milestones": get_my_milestones,
    "get_my_timelogs": get_my_timelogs,
    "create_task": create_task,
    "create_project": create_project,
}