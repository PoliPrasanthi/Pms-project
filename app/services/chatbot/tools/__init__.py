from .project_tools import get_my_projects, get_my_tasks


PROJECT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_my_projects",
            "description": "Get the projects assigned to the current user.",
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
            "description": "Get the tasks assigned to the current user.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
]


TOOL_FUNCTIONS = {
    "get_my_projects": get_my_projects,
    "get_my_tasks": get_my_tasks  
}