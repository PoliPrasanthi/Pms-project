from .project_tools import (
    create_issue,
    get_current_user_details,
    get_my_projects,
    get_my_tasks,
    get_my_tasklists,
    get_my_issues,
    get_my_milestones,
    get_my_timelogs,
    create_task,
    create_project,
    get_my_permissions,
    get_current_user_details,
)


PROJECT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_current_user_details",
            "description": (
                "Get the profile details of the currently authenticated user. "
                "Use this when the user asks questions such as "
                "'who am I', 'what is my name', 'show my profile', "
                "'tell me about myself', or asks for their personal profile details."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
    "type": "function",
        "function": {
            "name": "get_my_permissions",
            "description": (
                "Get the current user's PMS permissions. "
                "Use this when the user asks whether they can "
                "create, view, edit, delete, or otherwise access "
                "a PMS feature."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },

    {
        "type": "function",
        "function": {
            "name": "get_my_projects",
            "description": (
                " when user asks for list my projects or show my projects or what are my projects , "
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
                "MANDATORY creation workflow tool. "
                "Call this tool whenever the user actually wants to create, add, "
                "make, or start a new PMS record. "
                "This includes requests such as 'create a task', 'create a task?', "
                "'help me create a task', 'create a project', "
                "'add an issue', or 'create a milestone'. "
                "You MUST call this tool even when the user has provided NO fields "
                "and the arguments object is empty. "
                "Do NOT ask the user for fields yourself. "
                "Do NOT answer with a conversational message before calling this tool. "
                "Do NOT call this tool for permission questions such as "
                "'Can I create a task?', 'Do I have permission to create a task?', "
                "or 'Am I allowed to create a task?'. "
                "For those questions, answer only the permission result."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_type": {
                        "type": "string",
                        "enum": [
                            "tasklist",
                            "task",
                            "project",
                            "issue",
                            "milestone",
                        ],
                        "description": (
                            "The PMS entity the user wants to create."
                        ),
                    },
                    "arguments": {
                        "type": "object",
                        "description": (
                            "Only values explicitly provided by the user. "
                            "Use an empty object when the user has not provided "
                            "any creation fields."
                        ),
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
    "get_my_permissions",
    "get_current_user_details",
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
    "create_issue": create_issue,
    "get_my_permissions": get_my_permissions,
    "get_current_user_details": get_current_user_details, 
}