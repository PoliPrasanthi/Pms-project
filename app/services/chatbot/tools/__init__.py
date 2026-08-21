from .project_tools import (
    get_my_milestones,
    get_my_projects,
    get_my_tasks,
    get_my_tasklists,
    get_my_issues,
    check_create_project_permission,
    get_my_timelogs,
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
            "name": "check_create_project_permission",
            "description": (
                "Check whether the currently authenticated "
                "user has permission to create a project. "
                "Use this before creating a project."
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
            "description": "Get the milestones accessible to the current user.",
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
]


TOOL_FUNCTIONS = {

    "get_my_projects": get_my_projects,

    "get_my_tasks": get_my_tasks,

    "get_my_tasklists": get_my_tasklists,

    "get_my_issues": get_my_issues,

    "check_create_project_permission": check_create_project_permission,
    "get_my_milestones": get_my_milestones,
    "get_my_timelogs": get_my_timelogs,
}