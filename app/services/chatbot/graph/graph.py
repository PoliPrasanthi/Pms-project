import json
import html
import logging

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


logger = logging.getLogger(__name__)


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



def resolve_detail_followup_query(messages: list[dict], user_message: str) -> str | None:
    """
    Resolve short follow-ups such as 'more details about it' from the recent
    conversation. The resulting query is used to fetch fresh authorized PMS
    records; it does not reuse old tool data as a source of truth.
    """
    text = (user_message or "").strip().lower()
    detail_followup = bool(re.search(
        r"\b(?:more details|full details|complete details|all details|"
        r"details about it|details of it|tell me more|explain it|"
        r"what about its details|show details)\b",
        text,
    ))
    if not detail_followup:
        return None

    # Most recent assistant answer is the best clue for pronouns like "it".
    for message in reversed(messages[:-1] if messages else []):
        if message.get("role") != "assistant":
            continue
        content = str(message.get("content") or "").lower()
        for entity, patterns in (
            ("issues", (r"\bissues?\b", r"\bdefects?\b", r"\bbug\b")),
            ("tasks", (r"\btasks?\b",)),
            ("projects", (r"\bprojects?\b",)),
            ("milestones", (r"\bmilestones?\b",)),
            ("tasklists", (r"\btask\s*lists?\b", r"\btasklists?\b")),
        ):
            if any(re.search(pattern, content) for pattern in patterns):
                return f"show {entity} with full details"

    # If the assistant's last response doesn't identify the entity, inspect
    # earlier user turns, most recent first.
    for message in reversed(messages[:-1] if messages else []):
        if message.get("role") != "user":
            continue
        prior = str(message.get("content") or "").lower()
        for entity, patterns in (
            ("issues", (r"\bissues?\b", r"\bdefects?\b", r"\bbug\b")),
            ("tasks", (r"\btasks?\b",)),
            ("projects", (r"\bprojects?\b",)),
            ("milestones", (r"\bmilestones?\b",)),
            ("tasklists", (r"\btask\s*lists?\b", r"\btasklists?\b")),
        ):
            if any(re.search(pattern, prior) for pattern in patterns):
                return f"show {entity} with full details"
    return None


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
# CURRENT-USER FILTER + SAFE HTML FALLBACK
# ============================================================

CURRENT_USER_PHRASE = re.compile(
    r"\b(?:assigned|allocated|delegated|given)\s+to\s+me\b|"
    r"\b(?:assigned|allocated|delegated|given)\s+me\b|"
    r"\bmy\s+(?:assigned\s+)?(?:tasks?|projects?|issues?|defects?|milestones?|task\s*lists?|time\s*logs?)\b",
    re.IGNORECASE,
)

ENTITY_META = {
    "get_my_projects": ("project", "projects", ("project_name", "projectName", "name", "title")),
    "get_my_tasks": ("task", "tasks", ("task_name", "taskName", "name", "title")),
    "get_my_issues": ("issue", "issues", ("bug_name", "bugName", "issue_name", "issueName", "name", "title")),
    "get_my_milestones": ("milestone", "milestones", ("milestone_name", "milestoneName", "name", "title")),
    "get_my_tasklists": ("tasklist", "tasklists", ("tasklist_name", "task_list_name", "tasklistName", "name", "title")),
    "get_my_timelogs": ("timelog", "time-log", ("description", "task_name", "taskName", "name", "title")),
}


def _normal_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _walk_values(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key, item
            if isinstance(item, (dict, list)):
                yield from _walk_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_values(item)


def _current_user_identity_values(user):
    values = set()
    if not isinstance(user, (dict, list)):
        return values
    id_keys = {"id", "user_id", "userid", "pk", "user_pk"}
    name_keys = {"name", "full_name", "fullname", "first_name", "firstname", "last_name", "lastname", "username", "user_name", "email", "email_address"}
    # IDs are accepted only from the top-level authenticated-user object;
    # nested role/team IDs must not accidentally identify an assignment.
    if isinstance(user, dict):
        for key in id_keys:
            value = user.get(key)
            if value is not None and not isinstance(value, (dict, list)):
                values.add(_normal_text(value))
    for key, value in _walk_values(user):
        normalized_key = str(key).lower().replace("-", "_")
        if value is None or isinstance(value, (dict, list)):
            continue
        if normalized_key in name_keys:
            val = _normal_text(value)
            if val:
                values.add(val)
    # Build a full name from first/last name when supplied separately.
    if isinstance(user, dict):
        first = user.get("first_name") or user.get("firstName")
        last = user.get("last_name") or user.get("lastName")
        if first or last:
            values.add(_normal_text(f"{first or ''} {last or ''}"))
    return values


def _record_assignment_matches(record, entity_type, identities):
    if not isinstance(record, dict) or not identities:
        return False
    common = {
        "task": {"assignee", "assignees", "assignee_name", "assignee_names", "assignee_id", "assignee_ids", "assignee_email", "assignee_emails", "assigned_to", "assigned_to_id", "assigned_to_name", "assigned_user", "assigned_users", "user"},
        "issue": {"assignee", "assignees", "assignee_name", "assignee_id", "assignee_email", "assignee_emails", "assigned_to", "assigned_to_id", "assigned_user"},
        "project": {"team", "team_members", "members", "member", "users", "user_emails", "team_emails", "project_manager", "project_manager_name", "project_manager_id", "delivery_head", "delivery_head_name", "delivery_head_id", "owner", "owners", "owner_name", "owner_id", "assigned_to", "assigned_users"},
        "milestone": {"assignee", "assignees", "owner", "owner_name", "assigned_to", "user", "users"},
        "tasklist": {"assignee", "assignees", "owner", "owner_name", "assigned_to", "user", "users"},
        "timelog": {"user", "user_name", "username", "user_id", "logged_by", "logged_by_name", "employee", "employee_name", "created_by", "created_by_name"},
    }.get(entity_type, set())
    # Also recognize explicit assignment-related fields without matching unrelated project/task text.
    for key, value in _walk_values(record):
        key_norm = str(key).lower().replace("-", "_").replace(" ", "_")
        relevant = key_norm in common or any(token in key_norm for token in ("assignee", "assigned_to", "team_member", "user_email"))
        if entity_type == "project":
            relevant = relevant or any(token in key_norm for token in ("project_manager", "delivery_head", "team", "member", "owner"))
        if entity_type == "timelog":
            relevant = relevant or any(token in key_norm for token in ("logged_by", "employee", "user"))
        if not relevant or value is None:
            continue
        candidates = []
        if isinstance(value, (str, int, float)):
            candidates = [value]
        elif isinstance(value, list):
            candidates = value
        elif isinstance(value, dict):
            candidates = [v for k, v in value.items() if str(k).lower() in {"id", "user_id", "name", "full_name", "first_name", "last_name", "username", "email", "email_address"} and not isinstance(v, (dict, list))]
            first = value.get("first_name") or value.get("firstName")
            last = value.get("last_name") or value.get("lastName")
            if first or last:
                candidates.append(f"{first or ''} {last or ''}")
        for candidate in candidates:
            if isinstance(candidate, dict):
                for k, v in candidate.items():
                    if str(k).lower() in {"id", "user_id", "name", "full_name", "username", "email", "first_name", "last_name"} and v is not None:
                        if _normal_text(v) in identities:
                            return True
                first = candidate.get("first_name") or candidate.get("firstName")
                last = candidate.get("last_name") or candidate.get("lastName")
                if (first or last) and _normal_text(f"{first or ''} {last or ''}") in identities:
                    return True
            elif _normal_text(candidate) in identities:
                return True
    return False


def _extract_record_list(value):
    """Return the most likely record list and its parent/key, supporting common API wrappers."""
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return value, None, None
    if not isinstance(value, dict):
        return None, None, None
    for key in ("items", "results", "records", "data", "projects", "tasks", "issues", "milestones", "tasklists", "time_logs", "timelogs"):
        child = value.get(key)
        if isinstance(child, list) and (not child or all(isinstance(item, dict) for item in child)):
            return child, value, key
        if isinstance(child, dict):
            found = _extract_record_list(child)
            if found[0] is not None:
                return found[0], child, found[2]
    # API wrappers occasionally contain a single nested result object.
    for key, child in value.items():
        if isinstance(child, dict):
            found = _extract_record_list(child)
            if found[0] is not None:
                return found[0], child, found[2]
    return None, None, None


def _filter_records_for_current_user(payload, entity_type, identities):
    records, parent, key = _extract_record_list(payload)
    if records is None:
        return payload, None
    filtered = [r for r in records if _record_assignment_matches(r, entity_type, identities)]
    if parent is None:
        return filtered, len(filtered)
    parent[key] = filtered
    for count_key in ("total", "total_count", "filtered_count", "count"):
        if count_key in parent and isinstance(parent[count_key], (int, float)):
            parent[count_key] = len(filtered)
    if isinstance(payload, dict):
        payload["filtered_count"] = len(filtered)
    return payload, len(filtered)


def _record_date(record):
    for key in ("updated_at", "updatedAt", "modified_at", "modifiedAt", "created_at", "createdAt", "created_date", "createdDate", "start_date", "startDate", "date", "logged_date"):
        value = record.get(key) if isinstance(record, dict) else None
        if value:
            return str(value)
    return ""


def _safe_html_fallback(messages, user_message=""):
    """Format current-turn tool records as safe compact HTML if NVIDIA is unavailable."""
    current_user_index = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    turn = messages[current_user_index + 1:] if current_user_index >= 0 else messages
    tool_names = {}
    for message in turn:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls", []) or []:
                function = call.get("function", {}) or {}
                tool_names[call.get("id")] = function.get("name")
    chunks = []
    for message in turn:
        if message.get("role") != "tool":
            continue
        tool_name = tool_names.get(message.get("tool_call_id"))
        meta = ENTITY_META.get(tool_name)
        if not meta:
            continue
        entity_type, route, name_keys = meta
        try:
            payload = json.loads(message.get("content") or "null")
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("error"):
            chunks.append("<p>I couldn't verify the requested assignment filter because current-user details were unavailable. Please try again.</p>")
            continue
        records, _, _ = _extract_record_list(payload)
        if records is None:
            continue
        total = len(records)
        dated = any(_record_date(record) for record in records)
        shown = sorted(records, key=_record_date, reverse=True)[:5] if dated else records[:5]
        if not shown:
            chunks.append(f"<p>No matching {html.escape(route.replace('-', ' '))} found.</p>")
            continue
        title = "time logs" if entity_type == "timelog" else route.replace("-", " ")
        chunks.append(f"<p>Here are the {html.escape(title)}:</p><ol>")
        for record in shown:
            name = next((record.get(k) for k in name_keys if record.get(k) not in (None, "")), None)
            if name is None:
                name = f"{title[:-1].title() if title.endswith('s') else title.title()} record"
            record_id = next((record.get(k) for k in ("id", "pk") if record.get(k) not in (None, "")), None)
            public_id = next((record.get(k) for k in ("public_id", "publicId", "task_id", "project_id_sync", "issue_id") if record.get(k) not in (None, "")), None)
            name_html = html.escape(str(name))
            if record_id is not None:
                name_html = f'<a href="/{route}/{html.escape(str(record_id), quote=True)}">{name_html}</a>'
            suffix = f" ({html.escape(str(public_id))})" if public_id is not None else ""
            project_name = next((record.get(k) for k in ("project_name", "projectName") if record.get(k) not in (None, "")), None)
            project_id = next((record.get(k) for k in ("project_id", "projectId") if record.get(k) not in (None, "")), None)
            project_suffix = ""
            if entity_type != "project" and project_name:
                pname = html.escape(str(project_name))
                if project_id is not None:
                    pname = f'<a href="/projects/{html.escape(str(project_id), quote=True)}">{pname}</a>'
                project_suffix = f" — Project: {pname}"
            chunks.append(f"<li>{name_html}{suffix}{project_suffix}</li>")
        chunks.append("</ol>")
        if total > 5:
            chunks.append(f'<p>Showing 5 of {total} records. <a href="/{route}">View more {html.escape(title)}</a></p>')
    if not chunks:
        return None
    return {"response_type": "chat", "response": "".join(chunks), "data": []}


async def _current_user_details(access_token):
    getter = TOOL_FUNCTIONS.get("get_current_user_details")
    if getter:
        try:
            return await getter(access_token, {})
        except TypeError:
            return await getter(access_token)
    # Fallback to the existing authenticated PMS endpoint if the helper is not
    # registered as a callable tool. Never infer the current user from the prompt.
    import httpx
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            "http://127.0.0.1:8000/api/v1/chatbot/chatbot-user",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        )
        response.raise_for_status()
        return response.json()


def _wants_assigned_to_current_user(message):
    text = _normal_text(message)
    return bool(re.search(r"\b(?:assigned|allocated|delegated)\s+to\s+me\b|\b(?:assigned|allocated|delegated)\s+me\b", text))


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

    latest_user_index = -1
    for message_index in range(len(messages) - 1, -1, -1):
        if messages[message_index].get("role") == "user":
            latest_user_index = message_index
            break

    current_turn_messages = (
        messages[latest_user_index + 1:]
        if latest_user_index >= 0
        else messages
    )
    has_tool_result = any(
        message.get("role") == "tool"
        for message in current_turn_messages
    )

    routing_query = user_message
    if not has_tool_result:
        detail_query = resolve_detail_followup_query(messages, user_message)
        if detail_query:
            routing_query = detail_query

    read_tools = (
        detect_read_tools(routing_query)
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

    # NVIDIA failures must not be converted into empty search results or a
    # fabricated answer. Log the operation and let the registered FastAPI
    # exception handlers translate the exception into an HTTP response.
    try:
        response = await chat_with_nvidia(
            messages=messages,
            tools=PROJECT_TOOLS,
        )
    except Exception:
        logger.exception("NVIDIA request failed in llm_node")
        fallback = _safe_html_fallback(messages, user_message)
        if fallback:
            return {"messages": messages, "final_response": fallback}
        raise

    choices = response.get(
        "choices",
        [],
    )

    if not choices:
        logger.error("NVIDIA response in llm_node did not contain any choices")

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

            try:
                creation_response = await run_creation_graph(
                    access_token=state["access_token"],
                    current_user=current_user,
                    user_id=user_id,
                    session_id=session_id,
                    entity_type=entity_type,
                    action=state.get("action"),
                    arguments=creation_arguments,
                    form_data=state.get("form_data"),
                )
            except Exception:
                logger.exception(
                    "Creation workflow failed (entity_type=%s)",
                    entity_type,
                )
                # Do not return a success-shaped response when creation failed.
                raise

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

            try:
                result = await tool_function(
                    state["access_token"],
                    arguments,
                )
            except Exception:
                # Critical: a failed data request is not the same as a
                # successful request that returned zero records. Re-raise so
                # the API layer can report a failure instead of telling the
                # user that no matching tasks/projects were found.
                logger.exception(
                    "Read tool execution failed (tool=%s)",
                    tool_name,
                )
                raise

            # Only the explicit phrase "assigned to me" applies the current-user
            # filter. "What are my tasks/projects?" remains an unfiltered list request.
            latest_user_message = next((
                str(message.get("content") or "")
                for message in reversed(messages)
                if message.get("role") == "user"
            ), "")
            entity_meta = ENTITY_META.get(tool_name)
            if entity_meta and _wants_assigned_to_current_user(latest_user_message):
                entity_type = entity_meta[0]
                try:
                    current_user = await _current_user_details(state["access_token"])
                    identities = _current_user_identity_values(current_user)
                    if identities:
                        result, filtered_count = _filter_records_for_current_user(result, entity_type, identities)
                        logger.info("Applied current-user assignment filter (tool=%s, matched=%s)", tool_name, filtered_count)
                    else:
                        logger.warning("Current-user identity returned no matchable values; refusing to claim assignment-filtered results")
                        result = {"error": "Could not verify the current user's identity to filter assigned records."}
                except Exception:
                    logger.exception("Could not fetch current-user details for assignment filter")
                    result = {"error": "Could not verify the current user's identity to filter assigned records."}

            # Avoid logging the complete result: task/project payloads may
            # contain confidential business data. Record only safe metadata.
            result_count = len(result) if isinstance(result, (list, dict)) else None
            logger.debug(
                "Read tool completed successfully (tool=%s, result_type=%s, item_count=%s)",
                tool_name,
                type(result).__name__,
                result_count,
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
                    "4. For list requests, show at most the 5 most recent records (prefer updated_at/created_at/date descending; if no date exists, preserve TOOL DATA order). Use exactly one <ol> with one <li> per displayed record. If more than 5 records exist, append a View more link to the entity collection route (projects=/projects, tasks=/tasks, issues=/issues, milestones=/milestones, tasklists=/tasklists, time logs=/time-log) and state how many are shown.\n"
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

    logger.debug(
        "Prepared tool data for final response (tool_result_count=%s)",
        len(tool_data),
    )

    # TEMPORARY DEBUG PRINTS: inspect the exact PMS data being prepared for
    # the final LLM request. Remove or disable these after debugging because
    # tool results can contain internal/business information.
    print("\n" + "=" * 90, flush=True)
    print("[CHATBOT DEBUG] FINAL NODE INPUT", flush=True)
    print("[CHATBOT DEBUG] User question:", repr(user_message), flush=True)
    print("[CHATBOT DEBUG] Current-turn tool messages:", flush=True)
    for tool_index, tool_message in enumerate(
        [m for m in current_turn_messages if m.get("role") == "tool"],
        start=1,
    ):
        print(f"\n[CHATBOT DEBUG] TOOL RESULT #{tool_index}", flush=True)
        print(tool_message.get("content", "<no content>"), flush=True)

    print("\n[CHATBOT DEBUG] NAVIGATION-ENRICHED tool_data sent to final processing:", flush=True)
    print(json.dumps(tool_data, indent=2, ensure_ascii=False, default=str), flush=True)

    print("\n[CHATBOT DEBUG] Messages being sent to final NVIDIA call:", flush=True)
    for message_index, final_message in enumerate(final_messages, start=1):
        print(
            f"\n--- FINAL MESSAGE #{message_index} | role={final_message.get('role')} ---",
            flush=True,
        )
        print(final_message.get("content", "<no content>"), flush=True)

    print("=" * 90 + "\n", flush=True)

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

    # The final synthesis is a separate NVIDIA request and can fail even
    # after all tools succeeded. Propagate that failure instead of returning
    # a misleading "no results" or generic formatting response.
    try:
        final_response = await chat_with_nvidia(
            messages=final_messages,
            json_mode=True,
            max_tokens=2048,
        )
    except Exception:
        logger.exception("NVIDIA request failed in final_node")
        fallback = _safe_html_fallback(messages, user_message)
        if fallback:
            return {"final_response": fallback}
        raise

    # Print the raw NVIDIA response to compare its answer with the tool data.
    print("\n" + "=" * 90, flush=True)
    print("[CHATBOT DEBUG] RAW FINAL NVIDIA RESPONSE:", flush=True)
    print(json.dumps(final_response, indent=2, ensure_ascii=False, default=str), flush=True)
    print("=" * 90 + "\n", flush=True)

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

        logger.debug(
            "Received final LLM response content (length=%s)",
            len(content),
        )

        result = extract_json_object(
            content
        )

    if result is not None:

        normalized_result = normalize_final_response(result)
        print("\n[CHATBOT DEBUG] PARSED FINAL JSON BEFORE NAVIGATION:", flush=True)
        print(json.dumps(normalized_result, indent=2, ensure_ascii=False, default=str), flush=True)

        normalized_result["response"] = ensure_navigation_links(
            normalized_result.get("response", ""),
            tool_data,
        )

        if any(message.get("role") == "tool" for message in current_turn_messages):
            normalized_result["data"] = []

        print("\n[CHATBOT DEBUG] FINAL RESPONSE RETURNED TO FRONTEND:", flush=True)
        print(json.dumps(normalized_result, indent=2, ensure_ascii=False, default=str), flush=True)

        return {
            "final_response": normalized_result,
        }

    logger.warning(
        "Final LLM response did not contain a valid JSON object; using safe fallback"
    )
    fallback = _safe_html_fallback(messages, user_message)
    return {
        "final_response": fallback or {
            "response_type": "chat",
            "response": "<p>Unable to format the response. Please try again.</p>",
            "data": [],
        },
    }


# ============================================================
# GUARANTEE NAVIGATION LINKS IN FINAL HTML
# ============================================================

def ensure_navigation_links(response_html, tool_data):
    """Ensure task and project names link to their own records.

    A task's project is context, not the task itself. When the API returns a
    nested project object, use that project's own ID. When it returns flat
    fields (project_id + project_name), use those fields together. Never use a
    task ID to construct a project URL.
    """
    if not isinstance(response_html, str) or not response_html.strip():
        return response_html

    entities = []
    seen = set()

    def add_entity(entity_type, name, entity_id=None, url=None):
        if not entity_type or name is None:
            return
        name = str(name).strip()
        if not name:
            return
        if not url and entity_id is not None:
            route = ENTITY_ROUTE_BY_TYPE.get(entity_type)
            if route:
                url = f"/{route}/{entity_id}"
        if not url:
            return
        key = (entity_type, name.casefold(), str(url))
        if key not in seen:
            seen.add(key)
            entities.append((name, str(url)))

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
            add_entity(entity_type, name, entity_id, url)

        # Some task endpoints flatten project data instead of returning a
        # nested `project: {id, name}` object. Support these explicit pairs.
        # Only add a project link when its own project ID is available.
        if entity_type == "task":
            project_id = _first_non_empty(
                value,
                "project_id", "projectId", "project_pk", "projectPk",
            )
            project_name = _first_non_empty(
                value,
                "project_name", "projectName",
            )
            if project_name is None:
                project_value = value.get("project")
                if isinstance(project_value, str) and project_value.strip():
                    project_name = project_value
            project_url = _first_non_empty(
                value,
                "project_navigation_url", "projectNavigationUrl",
            )
            add_entity("project", project_name, project_id, project_url)

        for key, child in value.items():
            normalized = (
                str(key).strip().lower()
                .replace("-", "_")
                .replace(" ", "_")
            )
            child_hint = ENTITY_HINTS.get(normalized)
            if normalized in {"project", "project_details", "project_record"}:
                child_hint = "project"
            collect(child, child_hint)

    collect(tool_data)

    if not entities:
        return response_html

    try:
        from bs4 import BeautifulSoup, NavigableString

        soup = BeautifulSoup(response_html, "html.parser")
        # Prefer longer names first, so a shorter name contained in a longer
        # project/task name does not steal the match.
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
    except Exception:
        logger.exception("Navigation link post-processing failed")
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