import json
import html

from langgraph.graph import (
    StateGraph,
    START,
    END,
)

from app.services.chatbot.graph.state import ChatState
from app.services.chatbot.tools.prompt import FINAL_SYSTEM_PROMPT
from app.services.chatbot.nvidia_client import chat_with_nvidia
from app.services.chatbot.tools import (
    PROJECT_TOOLS,
    TOOL_FUNCTIONS,
    READ_TOOLS,
    CREATION_INTENT_TOOLS,
)

from app.services.chatbot.graph.creation_graph import (
    run_creation_graph,
)


# ============================================================
# PERMISSION CHECK
# ============================================================

def has_create_permission(
    permissions: dict,
    entity_type: str,
) -> bool:

    if not isinstance(permissions, dict):
        return False

    permission_key_map = {
        "project": "proj-create",
        "task": "task-create",
        "issue": "issue-create",
        "time": "time-create",
        "milestone": "milestone-create",
        "tasklist": "tasklist-create",
    }

    permission_key = permission_key_map.get(entity_type.lower())

    if not permission_key:
        return False

    permission_value = permissions.get(permission_key)

    if permission_value is True:
        return True

    if isinstance(permission_value, str):
        return permission_value.strip().lower() in {
            "true",
            "all",
            "a",
            "o",
        }

    return False
# ============================================================
# GREETINGS
# ============================================================

GREETINGS = {
    "hi",
    "hii",
    "hiii",
    "hello",
    "hey",
    "heyy",
    "good morning",
    "good afternoon",
    "good evening",
}


def is_simple_greeting(
    message: str,
) -> bool:

    normalized = (
        message
        .lower()
        .strip()
        .replace("!", "")
        .replace(".", "")
        .replace(",", "")
    )

    return normalized in GREETINGS


# ============================================================
# DETECT CREATION ENTITY
# ============================================================

import re


def detect_creation_entity(
    message: str,
) -> str | None:

    text = (
        message
        .lower()
        .strip()
    )

    # ---------------------------------------------------------
    # Detect the entity immediately after the creation action.
    # This prevents "project" mentioned as a context from
    # overriding the actual entity being created.
    # ---------------------------------------------------------

    pattern = re.search(
        r"\b(?:create|add|make|insert|new)\s+"
        r"(?:a\s+|an\s+)?"
        r"(tasklist|task\s+list|task|project|issue|milestone)\b",
        text,
    )

    if pattern:
        entity = pattern.group(1)

        if entity in {
            "tasklist",
            "task list",
        }:
            return "tasklist"

        return entity

    # ---------------------------------------------------------
    # Fallback
    # ---------------------------------------------------------

    if "tasklist" in text:
        return "tasklist"

    if "task list" in text:
        return "tasklist"

    if "task" in text:
        return "task"

    if "project" in text:
        return "project"

    if "issue" in text:
        return "issue"

    if "milestone" in text:
        return "milestone"

    return None

# ============================================================
# PERMISSION QUESTION
# ============================================================

def is_permission_question(
    message: str,
) -> bool:

    text = (
        message
        .lower()
        .strip()
    )

    permission_patterns = (
        "can i create",
        "can i add",
        "can i make",
        "do i have permission",
        "do i have the permission",
        "do i have access",
        "do i have permission to",
        "am i allowed",
        "am i permitted",
        "am i authorized",
        "is it possible for me to create",
        "is it possible for me to add",
        "is it possible for me to make",
    )

    if any(
        pattern in text
        for pattern in permission_patterns
    ):
        return True

    entity_type = detect_creation_entity(text)

    if entity_type:
        return (
            "permission" in text
            or "access" in text
            or "allowed" in text
            or "authorized" in text
            or "permitted" in text
        )

    return False


# ============================================================
# ACTUAL CREATION REQUEST
# ============================================================

def detect_read_tools(message: str) -> list[str]:
    """
    Determine which read tools are required for a PMS data question.

    This routing is deterministic so normal PMS data questions do not
    depend on the LLM deciding whether to emit a tool call. The LLM is
    still used after the tool results are returned to analyze, filter,
    compare, or sort the actual data.
    """

    text = (message or "").lower().strip()

    if not text:
        return []

    tools = []

    def has(patterns: tuple[str, ...]) -> bool:
        return any(
            re.search(pattern, text)
            for pattern in patterns
        )

    # General person lookup (e.g. "Who is Deepak?") searches both authorized
    # projects and tasks when the user did not specify an entity type.
    person_lookup = bool(re.search(
        r"\b(?:who\s+is|who\s+are|do\s+we\s+have|is\s+there|find|search\s+for|look\s+for)\s+"
        r"(?:anyone\s+named\s+|a\s+person\s+named\s+|any\s+record\s+with\s+the\s+name\s+)?"
        r"[a-z][a-z .'-]*\??$",
        text,
    ))

    if person_lookup and not has((
        r"\btasks?\b",
        r"\bprojects?\b",
        r"\bissues?\b",
        r"\bmilestones?\b",
        r"\btask\s*lists?\b",
    )):
        return ["get_my_projects", "get_my_tasks"]

    # Task lists must be checked separately so the word "task" inside
    # "task list" does not accidentally select get_my_tasks.
    wants_tasklists = has((
        r"\btask\s*lists?\b",
        r"\btasklists?\b",
    ))

    wants_tasks = has((
        r"\btasks?\b(?!\s*lists?\b)",
        r"\btask\s+(?:priority|status|owner|owners|assignee|assignees|due|deadline|deadlines)\b",
        r"\b(?:priority|priorities)\s+(?:of|for|on)\s+(?:my\s+)?tasks?\b",
    ))

    wants_projects = has((
        r"\bprojects?\b",
        r"\bproject\s+(?:status|manager|owner|team|members|priority|details?)\b",
    ))

    wants_issues = has((
        r"\bissues?\b",
        r"\bdefects?\b",
    ))

    wants_milestones = has((
        r"\bmilestones?\b",
    ))

    wants_timelogs = has((
        r"\btime\s*logs?\b",
        r"\btimesheets?\b",
        r"\btime\s*entries\b",
    ))

    # If a task is explicitly being requested in/under/for a project,
    # "project" is context for the task rather than a request to fetch
    # the project list.
    task_in_project_context = bool(
        wants_tasks
        and re.search(
            r"\btasks?\b.*\b(?:in|under|inside|within|for|of)\b.*\bproject\b",
            text,
        )
    )

    if wants_projects and not task_in_project_context:
        tools.append("get_my_projects")

    if wants_tasks:
        tools.append("get_my_tasks")

    if wants_tasklists:
        tools.append("get_my_tasklists")

    if wants_issues:
        tools.append("get_my_issues")

    if wants_milestones:
        tools.append("get_my_milestones")

    if wants_timelogs:
        tools.append("get_my_timelogs")

    return tools


# ============================================================
# ACTUAL CREATION REQUEST
# ============================================================

def is_actual_creation_request(
    message: str,
) -> bool:

    text = (
        message
        .lower()
        .strip()
    )

    if not text:
        return False

    if is_permission_question(text):
        return False

    creation_words = (
        "create",
        "add",
        "make",
        "insert",
        "new",
    )

    entity_type = detect_creation_entity(text)

    return (
        entity_type is not None
        and any(
            word in text
            for word in creation_words
        )
    )


# ============================================================
# LLM NODE
# ============================================================

async def llm_node(
    state: ChatState,
):

    messages = list(
        state.get("messages", [])
    )

    permissions = state.get(
        "permissions",
        {},
    )

    user_message = ""

    for message in reversed(messages):

        if message.get("role") == "user":

            user_message = (
                message.get(
                    "content",
                    "",
                )
                or ""
            )

            break

    # --------------------------------------------------------
    # GREETING
    # --------------------------------------------------------

    if is_simple_greeting(user_message):

        return {
            "messages": messages,
            "final_response": {
                "response_type": "chat",
                "response": (
                    "<p>Hello! How can I help you?</p>"
                ),
                "data": [],
            },
        }

    # --------------------------------------------------------
    # SESSION PERMISSION CONTEXT
    # --------------------------------------------------------

    permission_context_exists = any(
        message.get("role") == "system"
        and (
            "SESSION PERMISSIONS"
            in message.get(
                "content",
                "",
            )
        )
        for message in messages
    )

    if permissions and not permission_context_exists:

        messages.insert(
            0,
            {
                "role": "system",
                "content": (
                    "SESSION PERMISSIONS:\n"
                    + json.dumps(
                        permissions,
                        default=str,
                    )
                    + "\n\n"
                    "Use these permissions for permission questions. "
                    "A permission question such as 'Can I create a task?' "
                    "must be answered only as a permission question. "
                    "Do not call start_creation for a permission question. "
                    "When the user actually wants to create a record, "
                    "start_creation must be called. "
                    "Do not ask for creation fields yourself."
                ),
            },
        )

    # --------------------------------------------------------
    # PERMISSION QUESTION
    # --------------------------------------------------------

    if is_permission_question(
        user_message
    ):

        entity_type = detect_creation_entity(
            user_message
        )

        if entity_type:

            entity_labels = {
                "task": "task",
                "project": "project",
                "issue": "issue",
                "tasklist": "task list",
                "milestone": "milestone",
            }

            entity_label = entity_labels.get(
                entity_type,
                entity_type,
            )

            # IMPORTANT:
            # Use permissions stored in the current session.
            # Do NOT call get_my_permissions() again.

            allowed = has_create_permission(
                permissions=state.get(
                    "permissions",
                    {},
                ),
                entity_type=entity_type,
            )

            if allowed:

                return {
                    "messages": messages,
                    "final_response": {
                        "response_type": "chat",
                        "response": (
                            f"<p>Yes, you can create "
                            f"{entity_label}.</p>"
                        ),
                        "data": [],
                    },
                }

            return {
                "messages": messages,
                "final_response": {
                    "response_type": "chat",
                    "response": (
                        f"<p>No, you cannot create "
                        f"{entity_label}.</p>"
                    ),
                    "data": [],
                },
            }

    # --------------------------------------------------------
    # ACTUAL CREATION REQUEST
    # --------------------------------------------------------

    if is_actual_creation_request(
        user_message
    ):

        entity_type = detect_creation_entity(
            user_message
        )

        synthetic_tool_call = {
            "id": "creation_intent",
            "type": "function",
            "function": {
                "name": "start_creation",
                "arguments": json.dumps(
                    {
                        "entity_type": entity_type,
                        "arguments": {},
                    }
                ),
            },
        }

        synthetic_message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                synthetic_tool_call
            ],
        }

        return {
            "messages": messages + [
                synthetic_message
            ],
        }

        # --------------------------------------------------------
    # DETERMINISTIC READ TOOL ROUTING
    # --------------------------------------------------------
    #
    # Run deterministic read routing only on the first pass of a user turn.
    # Once a tool has returned data, let NVIDIA process that data. Otherwise
    # the same synthetic read_intent_* call is created repeatedly and the
    # graph loops forever.

    has_tool_result = any(
        message.get("role") == "tool"
        for message in messages
    )

    read_tools = (
        detect_read_tools(user_message)
        if not has_tool_result
        else []
    )

    if read_tools:


        synthetic_tool_calls = []

        for index, tool_name in enumerate(read_tools):
            synthetic_tool_calls.append(
                {
                    "id": f"read_intent_{index}",
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": "{}",
                    },
                }
            )

        synthetic_message = {
            "role": "assistant",
            "content": None,
            "tool_calls": synthetic_tool_calls,
        }

        return {
            "messages": messages + [
                synthetic_message
            ],
        }

    # --------------------------------------------------------
    # NVIDIA
    # --------------------------------------------------------

    response = await chat_with_nvidia(
        messages=messages,
        tools=PROJECT_TOOLS,
    )

    choices = response.get(
        "choices",
        [],
    )

    if not choices:

        return {
            "messages": messages,
            "final_response": {
                "response_type": "chat",
                "response": (
                    "<p>Sorry, I could not process your request.</p>"
                ),
                "data": [],
            },
        }

    message = choices[0].get(
        "message",
        {},
    )

    return {
        "messages": messages + [
            message
        ],
    }


# ============================================================
# ROUTE AFTER LLM
# ============================================================

def route_after_llm(
    state: ChatState,
):

    if state.get("final_response"):
        return "final"

    messages = state.get(
        "messages",
        [],
    )

    if not messages:
        return "final"

    last_message = messages[-1]

    if last_message.get("tool_calls"):
        return "tools"

    return "final"


# ============================================================
# TOOL NODE
# ============================================================

async def tool_node(
    state: ChatState,
):

    messages = state.get(
        "messages",
        [],
    )

    if not messages:

        return {
            "messages": messages,
        }

    last_message = messages[-1]

    tool_calls = last_message.get(
        "tool_calls",
        [],
    )

    tool_messages = []

    for tool_call in tool_calls:

        function = tool_call.get(
            "function",
            {},
        )

        tool_name = function.get(
            "name",
        )

        arguments = function.get(
            "arguments",
            {},
        )

        if isinstance(
            arguments,
            str,
        ):

            try:

                arguments = json.loads(
                    arguments
                )

            except json.JSONDecodeError:

                arguments = {}

        if not isinstance(
            arguments,
            dict,
        ):

            arguments = {}

        # ====================================================
        # CREATION INTENT
        # ====================================================

        if tool_name in CREATION_INTENT_TOOLS:

            entity_type = arguments.get(
                "entity_type",
            )

            creation_arguments = arguments.get(
                "arguments",
                {},
            )

            if not isinstance(
                creation_arguments,
                dict,
            ):

                creation_arguments = {}

            # ------------------------------------------------
            # FALLBACK ENTITY DETECTION
            # ------------------------------------------------

            if not entity_type:

                user_message = ""

                for message in reversed(
                    messages
                ):

                    if (
                        message.get("role")
                        == "user"
                    ):

                        user_message = (
                            message.get(
                                "content",
                                "",
                            )
                            or ""
                        )

                        break

                entity_type = detect_creation_entity(
                    user_message
                )

            if not entity_type:

                return {
                    "messages": messages,
                    "final_response": {
                        "response_type": "chat",
                        "response": (
                            "<p>Unable to determine "
                            "what you want to create.</p>"
                        ),
                        "data": [],
                    },
                }

            entity_type = str(
                entity_type
            ).strip().lower()

            # =================================================
            # IMPORTANT PERMISSION CHECK
            # =================================================
            #
            # DO NOT call get_my_permissions() here.
            #
            # Use the permissions belonging to the
            # current user's MongoDB session.
            #
            # =================================================

            allowed = has_create_permission(
                permissions=state.get(
                    "permissions",
                    {},
                ),
                entity_type=entity_type,
            )

            if not allowed:

                return {
                    "messages": messages,
                    "final_response": {
                        "response_type": "chat",
                        "response": (
                            f"<p>No, you do not have "
                            f"permission to create "
                            f"{entity_type}.</p>"
                        ),
                        "data": [],
                    },
                }

            # ------------------------------------------------
            # CURRENT USER
            # ------------------------------------------------

            current_user = state.get(
                "current_user"
            )

            user_id = state.get(
                "user_id"
            )

            session_id = state.get(
                "session_id"
            )

            if (
                user_id is None
                and isinstance(
                    current_user,
                    dict,
                )
            ):

                user_id = current_user.get(
                    "id"
                )

            if (
                user_id is None
                or session_id is None
            ):

                return {
                    "messages": messages,
                    "final_response": {
                        "response_type": "chat",
                        "response": (
                            "<p>I could not start "
                            "the creation workflow "
                            "because the chatbot "
                            "session is incomplete.</p>"
                        ),
                        "data": [],
                    },
                }

            # ------------------------------------------------
            # CREATION GRAPH
            # ------------------------------------------------

            creation_response = (
                await run_creation_graph(
                    access_token=state[
                        "access_token"
                    ],
                    current_user=current_user,
                    user_id=user_id,
                    session_id=session_id,
                    entity_type=entity_type,
                    action=state.get(
                        "action"
                    ),
                    arguments=creation_arguments,
                    form_data=state.get(
                        "form_data"
                    ),
                )
            )

            return {
                "messages": messages,
                "final_response": creation_response,
            }

        # ====================================================
        # NORMAL TOOLS
        # ====================================================

        tool_function = TOOL_FUNCTIONS.get(
            tool_name,
        )

        if not tool_function:

            result = {
                "error": (
                    f"Unknown tool: {tool_name}"
                )
            }

        elif tool_name in READ_TOOLS:

            result = await tool_function(
                state["access_token"],
                arguments,
            )

            print(
                "\n"
                + "=" * 70
            )

            print(
                "[GRAPH] TOOL OUTPUT"
            )

            print(
                "=" * 70
            )

            print(
                f"[GRAPH] Tool: {tool_name}"
            )

            print(
                json.dumps(
                    result,
                    indent=2,
                    default=str,
                )
            )

            print(
                "=" * 70
            )

        else:

            result = {
                "error": (
                    f"Tool {tool_name} is not "
                    "available to the main chat graph."
                )
            }

        tool_messages.append(
            {
                "role": "tool",
                "content": json.dumps(
                    result,
                    default=str,
                ),
                "tool_call_id": tool_call.get(
                    "id",
                ),
            }
        )

    return {
        "messages": messages + tool_messages
    }


# ============================================================
# ROUTE AFTER TOOLS
# ============================================================

def route_after_tools(
    state: ChatState,
):

    if state.get("final_response"):
        return "final"

    # Always return tool results to the LLM.
    # The LLM decides whether to analyze the result, answer the user,
    # or call another available tool.
    return "llm"


# ============================================================
# JSON EXTRACTION
# ============================================================

def extract_json_object(
    content: str,
):

    if not content:
        return None

    cleaned = content.strip()

    if cleaned.startswith("```json"):

        cleaned = cleaned[
            len("```json"):
        ]

        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]

    elif cleaned.startswith("```"):

        cleaned = cleaned[
            len("```"):
        ]

        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]

    cleaned = cleaned.strip()

    try:

        return json.loads(
            cleaned
        )

    except json.JSONDecodeError:

        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")

    if (
        start != -1
        and end != -1
        and end > start
    ):

        try:

            return json.loads(
                cleaned[
                    start:end + 1
                ]
            )

        except json.JSONDecodeError:

            pass

    return None


# ============================================================
# NORMALIZE FINAL RESPONSE
# ============================================================

def normalize_final_response(
    result,
):

    if not isinstance(
        result,
        dict,
    ):
        return None

    result["response_type"] = "chat"
    result["response"] = result.get(
        "response",
        "<p>I couldn't format the response.</p>",
    )

    # The frontend needs the formatted response. Do not make the LLM
    # repeat the complete tool payload, which can cause truncation.
    result["data"] = []

    return result


# ============================================================
# HTML VALUE
# ============================================================

def _html_value(
    value,
    level=0,
):

    if value is None:
        return ""

    if isinstance(
        value,
        bool,
    ):

        return (
            "true"
            if value
            else "false"
        )

    if isinstance(
        value,
        (
            str,
            int,
            float,
        ),
    ):

        return html.escape(
            str(value)
        )

    if level > 6:

        return html.escape(
            str(value)
        )

    if isinstance(
        value,
        list,
    ):

        if not value:

            return (
                "<p>No records returned.</p>"
            )

        parts = [
            "<ul>"
        ]

        for item in value[:100]:

            parts.append(
                "<li>"
            )

            parts.append(
                _html_value(
                    item,
                    level + 1,
                )
            )

            parts.append(
                "</li>"
            )

        if len(value) > 100:

            parts.append(
                f"<li>Showing first 100 "
                f"of {len(value)} records.</li>"
            )

        parts.append(
            "</ul>"
        )

        return "".join(parts)

    if isinstance(
        value,
        dict,
    ):

        if not value:

            return (
                "<p>No data returned.</p>"
            )

        parts = [
            "<div>"
        ]

        for key, item in value.items():

            label = html.escape(
                str(key)
                .replace(
                    "_",
                    " ",
                )
                .title()
            )

            parts.append(
                '<div style="margin-bottom:6px;">'
            )

            parts.append(
                f"<strong>{label}:</strong> "
            )

            if isinstance(
                item,
                (
                    dict,
                    list,
                ),
            ):

                parts.append(
                    _html_value(
                        item,
                        level + 1,
                    )
                )

            else:

                parts.append(
                    _html_value(
                        item,
                        level + 1,
                    )
                )

            parts.append(
                "</div>"
            )

        parts.append(
            "</div>"
        )

        return "".join(parts)

    return html.escape(
        str(value)
    )


# ============================================================
# TOOL RESULTS HTML
# ============================================================

# ============================================================
# EXTRACT TOOL DATA
# ============================================================

def extract_tool_data(
    messages
):

    data = []

    for message in messages:

        if message.get(
            "role"
        ) != "tool":

            continue

        raw_content = (
            message.get(
                "content",
                "",
            )
            or ""
        )

        try:

            parsed = json.loads(
                raw_content
            )

        except (
            TypeError,
            json.JSONDecodeError,
        ):

            parsed = raw_content

        data.append(
            parsed
        )

    return data


# ============================================================
# NAVIGATION URLS
# ============================================================

ENTITY_ROUTES = {
    "projects": "projects",
    "project": "projects",
    "tasks": "tasks",
    "task": "tasks",
    "issues": "issues",
    "issue": "issues",
    "tasklists": "tasklists",
    "task_lists": "tasklists",
    "tasklist": "tasklists",
    "milestones": "milestones",
    "milestone": "milestones",
}

ENTITY_NAME_KEYS = {
    "project": ("project_name", "projectName"),
    "task": ("task_name", "taskName"),
    "issue": ("issue_name", "issueName", "bug_name", "bugName"),
    "milestone": ("milestone_name", "milestoneName"),
    "tasklist": (
        "tasklist_name",
        "task_list_name",
        "tasklistName",
        "taskListName",
    ),
}

ENTITY_ROUTE_BY_TYPE = {
    "project": "projects",
    "task": "tasks",
    "issue": "issues",
    "milestone": "milestones",
    "tasklist": "tasklists",
}

ENTITY_HINTS = {
    "project": "project",
    "projects": "project",
    "project_details": "project",
    "project_record": "project",
    "task": "task",
    "tasks": "task",
    "task_details": "task",
    "task_record": "task",
    "issue": "issue",
    "issues": "issue",
    "issue_details": "issue",
    "issue_record": "issue",
    "defect": "issue",
    "defects": "issue",
    "milestone": "milestone",
    "milestones": "milestone",
    "milestone_details": "milestone",
    "milestone_record": "milestone",
    "tasklist": "tasklist",
    "tasklists": "tasklist",
    "task_list": "tasklist",
    "task_lists": "tasklist",
    "tasklist_details": "tasklist",
    "task_list_details": "tasklist",
    "tasklist_record": "tasklist",
    "task_list_record": "tasklist",
}


def _first_non_empty(record, *keys):
    if not isinstance(record, dict):
        return None
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return None


def _detect_entity_type(record, hinted_type=None):
    # A structural hint (e.g. `tasks`) is stronger than a nested
    # `project_name` field on a task/issue record.
    if hinted_type:
        return hinted_type

    # Prefer the most specific entity fields before project_name because
    # task/issue/etc. records commonly also contain project_name.
    for entity_type in (
        "task",
        "issue",
        "milestone",
        "tasklist",
        "project",
    ):
        if _first_non_empty(record, *ENTITY_NAME_KEYS[entity_type]):
            return entity_type

    entity_type_value = _first_non_empty(
        record,
        "entity_type",
        "entityType",
        "type",
    )
    if entity_type_value:
        normalized = str(entity_type_value).strip().lower().replace("-", "_").replace(" ", "_")
        if normalized in ENTITY_ROUTES:
            return normalized.rstrip("s")
        if normalized in ENTITY_HINTS:
            return ENTITY_HINTS[normalized]

    return None


def _add_navigation_to_record(record, entity_type):
    if not isinstance(record, dict) or not entity_type:
        return record

    route = ENTITY_ROUTE_BY_TYPE.get(entity_type)
    if not route:
        return record

    existing_url = _first_non_empty(
        record,
        "navigation_url",
        "navigationUrl",
    )
    record_id = _first_non_empty(record, "id", "pk")

    if not existing_url and record_id is not None:
        record["navigation_url"] = f"/{route}/{record_id}"

    return record


def add_navigation_urls(value, hinted_type=None):
    """Add navigation URLs consistently, regardless of which PMS wrapper contains the entity records."""

    if isinstance(value, list):
        return [add_navigation_urls(item, hinted_type) for item in value]

    if not isinstance(value, dict):
        return value

    entity_type = _detect_entity_type(value, hinted_type)
    result = dict(value)

    if entity_type:
        _add_navigation_to_record(result, entity_type)

    for key, child in list(result.items()):
        normalized = (
            str(key)
            .strip()
            .lower()
            .replace("-", "_")
            .replace(" ", "_")
        )
        child_hint = ENTITY_HINTS.get(normalized)
        result[key] = add_navigation_urls(child, child_hint)

    return result

STRICT_FINAL_OUTPUT_CONTRACT = (
    "FINAL OUTPUT CONTRACT — FOLLOW EXACTLY:\n"
    "For a normal list/show/get request, display ONLY the compact default fields for that entity.\n"
    "PROJECT: project name + public_id (or id). NOTHING ELSE unless explicitly requested.\n"
    "TASK: task name + public_id (or id) + project name. NOTHING ELSE unless explicitly requested.\n"
    "ISSUE: issue name + public_id (or id) + project name. NOTHING ELSE unless explicitly requested.\n"
    "MILESTONE: milestone name + public_id (or id) + project name. NOTHING ELSE unless explicitly requested.\n"
    "TASKLIST: tasklist name + public_id (or id) + project name. NOTHING ELSE unless explicitly requested.\n"
    "NEVER display status, priority, completion, dates, owner, assignee, manager, delivery head, team, description, hours, billing, timestamps, or other fields for a normal list request.\n"
    "Only add a field when the user explicitly asks for that field. Include only the requested fields plus the minimum identifying context.\n"
    "For multiple records, use exactly one <ol> and one <li> per record. Preserve TOOL DATA order. Do not sort unless requested.\n"
    "PRIMARY ENTITY NAVIGATION IS MANDATORY when a reliable id exists: render the primary entity name as <a href=\"navigation_url\">...</a>. The graph enriches reliable ids with the correct route before this prompt is sent.\n"
    "For a task-list response, link only each task name to its own task navigation_url. Do not link project names or create project links from task records.\n"
    "Never show raw URLs, raw JSON, tool names, internal metadata, or unrelated fields.\n"
    "Return exactly the required JSON object with response_type=chat, user-facing HTML in response, and data=[] for normal display/list responses."
)


# ============================================================
# FALLBACK FORMATTER
# ============================================================

# ============================================================
# FINAL NODE
# ============================================================

async def final_node(
    state: ChatState,
):

    existing_response = state.get(
        "final_response"
    )

    if existing_response:

        return {
            "final_response": existing_response,
        }

    messages = state.get(
        "messages",
        [],
    )

    user_message = ""
    user_message_index = -1

    for index in range(
        len(messages) - 1,
        -1,
        -1,
    ):

        if (
            messages[index].get(
                "role"
            )
            == "user"
        ):

            user_message = (
                messages[index].get(
                    "content",
                    "",
                )
                or ""
            )

            user_message_index = index

            break

    current_turn_messages = []

    if user_message_index != -1:

        current_turn_messages = messages[
            user_message_index + 1:
        ]

    final_messages = [
        {
            "role": "system",
            "content": FINAL_SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": user_message,
        },
    ]

    permissions = state.get(
        "permissions",
        {},
    )

    if permissions:

        final_messages.append(
            {
                "role": "system",
                "content": (
                    "SESSION PERMISSIONS:\n"
                    + json.dumps(
                        permissions,
                        default=str,
                    )
                ),
            }
        )

    if any(
        message.get(
            "role"
        ) == "tool"
        for message in current_turn_messages
    ):

        final_messages.append(
            {
                "role": "system",
                "content": (
                    "HARD TOOL RESULT PRESENTATION RULES — FOLLOW EXACTLY:\n"
                    "1. TOOL DATA is the source of truth. Never invent, change, sort, merge, duplicate, or remove records unless the user explicitly requests an operation.\n"
                    "2. NEVER dump all available fields. For normal list/show/get requests use only the compact default fields:\n"
                    "PROJECT = name + public_id (or id) ONLY.\n"
                    "TASK = name + public_id (or id) + project ONLY.\n"
                    "ISSUE = name + public_id (or id) + project ONLY.\n"
                    "MILESTONE = name + public_id (or id) + project ONLY.\n"
                    "TASKLIST = name + public_id (or id) + project ONLY.\n"
                    "3. Status, priority, completion, dates, owner, assignee, manager, delivery head, team, description, hours, billing, timestamps, and all other fields are FORBIDDEN by default. Add a field ONLY when the user explicitly asks for it.\n"
                    "4. Multiple records MUST use exactly one <ol> with one <li> per record, preserving TOOL DATA order.\n"
                    "5. PRIMARY ENTITY NAVIGATION is mandatory whenever a reliable entity id exists. Use the graph-supplied navigation_url on the entity name.\n"
                    "6. For a task-list response, link only each task name to its own task navigation_url. Do not link project names or create project links from task records.\n"
                    "7. Never show raw URLs, raw JSON, tool names, internal metadata, or unrelated fields.\n"
                    "8. Return response_type=chat, user-facing HTML in response, and data=[] for normal list/display responses.\n"
                ),
            }
        )

    # Give the final LLM the SAME navigation-enriched tool data that the
    # graph uses for validation. Previously the graph enriched `tool_data`
    # only for logging/fallback, but sent the ORIGINAL tool message to the
    # final LLM. That made navigation depend on whether a particular user/tool
    # response already contained `navigation_url`.
    for message in current_turn_messages:

        if message.get("role") == "assistant":
            final_messages.append(message)
            continue

        if message.get("role") != "tool":
            continue

        raw_content = message.get("content", "") or ""

        try:
            parsed_content = json.loads(raw_content)
            enriched_content = add_navigation_urls(parsed_content)
            final_messages.append(
                {
                    **message,
                    "content": json.dumps(
                        enriched_content,
                        default=str,
                    ),
                }
            )
        except (TypeError, json.JSONDecodeError):
            final_messages.append(message)

    if any(
        message.get("role") == "tool"
        for message in current_turn_messages
    ):
        final_messages.append(
            {
                "role": "system",
                "content": STRICT_FINAL_OUTPUT_CONTRACT,
            }
        )

    tool_data = add_navigation_urls(
        extract_tool_data(
            current_turn_messages
        )
    )

    print(
        "\n"
        + "=" * 70
    )

    print(
        "[GRAPH] TOOL DATA FOR FINAL RESPONSE"
    )

    print(
        "=" * 70
    )

    print(
        json.dumps(
            tool_data,
            indent=2,
            default=str,
        )
    )

    print(
        "=" * 70
    )

    final_messages.append(
        {
            "role": "system",
            "content": (
                "FINAL RESPONSE MUST BE COMPACT. "
                "Return only the requested user-facing HTML in response. "
                "For normal list/display requests, data MUST be an empty array []. "
                "Do not repeat tool records in data. "
                "Do not include raw JSON or extra explanation."
            ),
        }
    )

    final_response = await chat_with_nvidia(
        messages=final_messages,
        json_mode=True,
        max_tokens=2048,
    )

    choices = final_response.get(
        "choices",
        [],
    )

    result = None

    if choices:

        final_message = choices[0].get(
            "message",
            {},
        )

        content = (
            final_message.get(
                "content",
                "",
            )
            or ""
        ).strip()

        print(
            "\n"
            + "=" * 70
        )

        print(
            "[GRAPH] FINAL LLM RESULT"
        )

        print(
            "=" * 70
        )

        print(content)

        print(
            "=" * 70
        )

        result = extract_json_object(
            content
        )

    if result is not None:

        normalized_result = normalize_final_response(result)
        normalized_result["response"] = ensure_navigation_links(
            normalized_result.get("response", ""),
            tool_data,
        )

        if any(message.get("role") == "tool" for message in current_turn_messages):
            normalized_result["data"] = []

        return {
            "final_response": normalized_result,
        }

    return {
        "final_response": {
            "response_type": "chat",
            "response": "<p>Unable to generate the response.</p>",
            "data": [],
        },
    }


# ============================================================
# GUARANTEE NAVIGATION LINKS IN FINAL HTML
# ============================================================

def ensure_navigation_links(response_html, tool_data):
    """Add primary-entity links without deriving project links from task records."""

    if not isinstance(response_html, str) or not response_html.strip():
        return response_html

    entities = []
    seen = set()

    def collect(value, hinted_type=None):
        if isinstance(value, list):
            for item in value:
                collect(item, hinted_type)
            return

        if not isinstance(value, dict):
            return

        entity_type = _detect_entity_type(value, hinted_type)
        if entity_type:
            name = _first_non_empty(value, *ENTITY_NAME_KEYS[entity_type])
            entity_id = _first_non_empty(value, "id", "pk")
            url = _first_non_empty(value, "navigation_url", "navigationUrl")

            # Build a URL only from this record's own ID and entity route.
            # Never use a task ID as a project ID.
            if entity_id is not None and not url:
                route = ENTITY_ROUTE_BY_TYPE.get(entity_type)
                if route:
                    url = f"/{route}/{entity_id}"

            if name and url:
                key = (entity_type, str(name).strip().casefold(), str(url))
                if key not in seen:
                    seen.add(key)
                    entities.append((str(name).strip(), str(url)))

        for key, child in value.items():
            normalized = (
                str(key).strip().lower()
                .replace("-", "_")
                .replace(" ", "_")
            )

            # A project's name/details may be included as context inside a task.
            # Do not turn that context into a project navigation link.
            if entity_type == "task" and normalized in {
                "project",
                "project_details",
                "project_record",
            }:
                continue

            collect(child, ENTITY_HINTS.get(normalized))

    collect(tool_data)

    if not entities:
        return response_html

    try:
        from bs4 import BeautifulSoup, NavigableString

        soup = BeautifulSoup(response_html, "html.parser")
        entities.sort(key=lambda item: len(item[0]), reverse=True)

        def replace_in_text_node(text_node):
            original = str(text_node)
            if not original.strip():
                return

            parent = text_node.parent
            if parent and parent.name == "a":
                return

            matches = []
            lower_original = original.casefold()

            for name, url in entities:
                start = 0
                needle = name.casefold()
                while True:
                    index = lower_original.find(needle, start)
                    if index < 0:
                        break
                    matches.append((index, index + len(name), url))
                    start = index + len(name)

            if not matches:
                return

            matches.sort(key=lambda item: (item[0], -(item[1] - item[0])))
            accepted = []
            cursor = -1
            for match_start, match_end, url in matches:
                if match_start >= cursor:
                    accepted.append((match_start, match_end, url))
                    cursor = match_end

            if not accepted:
                return

            nodes = []
            cursor = 0
            for match_start, match_end, url in accepted:
                if match_start > cursor:
                    nodes.append(NavigableString(original[cursor:match_start]))
                anchor = soup.new_tag("a", href=url)
                anchor.string = original[match_start:match_end]
                nodes.append(anchor)
                cursor = match_end

            if cursor < len(original):
                nodes.append(NavigableString(original[cursor:]))

            for node in nodes:
                text_node.insert_before(node)
            text_node.extract()

        for text_node in list(soup.find_all(string=True)):
            replace_in_text_node(text_node)

        return str(soup)

    except Exception as exc:
        print("[GRAPH] Navigation post-processing failed:", exc)
        return response_html


# ============================================================
# BUILD CHAT GRAPH
# ============================================================

def build_chat_graph():

    graph = StateGraph(
        ChatState
    )

    graph.add_node(
        "llm",
        llm_node,
    )

    graph.add_node(
        "tools",
        tool_node,
    )

    graph.add_node(
        "final",
        final_node,
    )

    graph.add_edge(
        START,
        "llm",
    )

    graph.add_conditional_edges(
        "llm",
        route_after_llm,
        {
            "tools": "tools",
            "final": "final",
        },
    )

    graph.add_conditional_edges(
        "tools",
        route_after_tools,
        {
            "llm": "llm",
            "final": "final",
        },
    )

    graph.add_edge(
        "final",
        END,
    )

    return graph.compile()


chat_graph = build_chat_graph()