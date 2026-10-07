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
    # Normal PMS data questions must reach the backend even when the
    # LLM does not emit a tool call by itself. Creation handling above
    # remains unchanged.
    #
    # Examples:
    #   "show my tasks" -> get_my_tasks
    #   "sort my tasks by priority" -> get_my_tasks
    #   "show my projects and tasks" -> both tools
    #
    # The final LLM still performs the requested analysis/sorting after
    # the real tool data is returned.

    read_tools = detect_read_tools(user_message)

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

    # Creation workflows already return their final response.
    # Normal read tools must go directly to the final node.
    # Sending them back to llm_node would run deterministic read
    # routing again and call the same tool repeatedly.
    return "final"


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
    # "tasklists": "tasklists",
    # "task_lists": "tasklists",
    # "tasklist": "tasklists",
    "milestones": "milestones",
    "milestone": "milestones",
}


def add_navigation_urls(value):
    """Add frontend links without filtering or rebuilding entity lists."""

    if isinstance(value, list):
        return [add_navigation_urls(item) for item in value]

    if not isinstance(value, dict):
        return value

    result = {
        key: add_navigation_urls(item)
        for key, item in value.items()
    }

    for key, route in ENTITY_ROUTES.items():
        records = result.get(key)

        if isinstance(records, list):
            for record in records:
                if isinstance(record, dict) and record.get("id") not in (None, ""):
                    record.setdefault(
                        "navigation_url",
                        f"/{route}/{record['id']}",
                    )

        elif isinstance(records, dict) and records.get("id") not in (None, ""):
            records.setdefault(
                "navigation_url",
                f"/{route}/{records['id']}",
            )

    return result

STRICT_FINAL_OUTPUT_CONTRACT = (
    "FINAL OUTPUT CONTRACT — FOLLOW EXACTLY:\n"
    "For list queries, return ONE ordered HTML list using <ol> and one <li> per record. "
    "Preserve TOOL DATA record order unless the user explicitly requests sorting, ranking, filtering, or another ordering. For an explicit ordering request, perform that operation using only TOOL DATA. Do not invent, merge, or duplicate records.\n"
    "Project list exact order: linked Project Name (Public ID) — Manager: ... — Delivery Head: ... — Status: ... — Priority: ... — Expected: Start Date – End Date — Team: .... "
    "Display no Id, Description, Billing Type, timestamps, or other fields.\n"
    "Task list exact order: linked Task Name (Task/Public ID) — Project: ... — Status: ... — Priority: ... — Assignee: ... — Due: ....\n"
    "Issue list exact order: linked Issue Name (Issue/Public ID) — Project: ... — Status: ... — Priority: ... — Assignee: ... — Due: ....\n"
    "Milestone list exact order: linked Milestone Name (Milestone/Public ID) — Project: ... — Status: ... — Due: ....\n"
    "Task List exact order: linked Task List Name — Project: ... — Description: ... when available.\n"
    "The primary entity name MUST be a clickable HTML <a> using navigation_url. If navigation_url is absent and id exists, use /projects/{id}, /tasks/{id}, /issues/{id}, /tasklists/{id}, or /milestones/{id} according to entity type. Never show the URL as text.\n"
    "Do not output raw JSON, tool names, internal metadata, or fields outside the applicable format unless explicitly requested."
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
                    "1. TOOL DATA is the source of truth. Never invent or change values.\n"
                    "2. The backend has already filtered the records. Preserve every record unless the user explicitly requests sorting, ranking, filtering, or another ordering. For an explicit ordering request, perform it using only TOOL DATA. Do not invent, merge, duplicate, or remove records.\n"
                    "3. Never dump all available fields. Display ONLY the fields specified for the matching list type.\n"
                    "4. PROJECT LIST: output ONLY one ordered HTML list (<ol>) with one <li> per project. EXACT order inside each item: linked Project Name (Public ID) — Manager: ... — Delivery Head: ... — Status: ... — Priority: ... — Expected: Start Date – End Date — Team: Member 1, Member 2. Do NOT display Id, Description, Billing Type, timestamps, or any other fields.\n"
                    "5. TASK LIST: output ONLY one ordered HTML list. EXACT order: linked Task Name (Public/Task ID) — Project: ... — Status: ... — Priority: ... — Assignee: ... — Due: ....\n"
                    "6. ISSUE LIST: output ONLY one ordered HTML list. EXACT order: linked Issue Name (Public/Issue ID) — Project: ... — Status: ... — Priority: ... — Assignee: ... — Due: ....\n"
                    "7. MILESTONE LIST: output ONLY one ordered HTML list. EXACT order: linked Milestone Name (Public/Milestone ID) — Project: ... — Status: ... — Due: ....\n"
                    "8. TASK LIST ENTITY: output ONLY one ordered HTML list. EXACT order: linked Task List Name — Project: ... — Description: ... when available.\n"
                    "9. Every list response MUST use <ol><li>...</li></ol>. Never use plain paragraphs, bullets, tables, or an unnumbered list for list queries.\n"
                    "10. Navigation is mandatory when an entity id is available. The PRIMARY ENTITY NAME must be an HTML <a> using navigation_url. If navigation_url is missing, do not create or guess a URL. Never show a guessed URL as plain text.\n"
                    "11. Do not display tool names, raw JSON, internal metadata, or fields outside the required format unless the user explicitly asks for them.\n"
                    "12. The response field must contain simple user-facing HTML. The data field MUST be [] for normal display/list responses; do not copy tool records into data.\n"
                ),
            }
        )

    for message in current_turn_messages:

        if message.get(
            "role"
        ) in {
            "assistant",
            "tool",
        }:

            final_messages.append(
                message
            )

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

        return {
            "final_response": (
                normalize_final_response(
                    result
                )
            ),
        }

    return {
        "final_response": {
            "response_type": "chat",
            "response": "<p>Unable to generate the response.</p>",
            "data": tool_data,
        },
    }


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