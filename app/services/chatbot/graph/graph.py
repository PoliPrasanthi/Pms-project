import json
import html
import logging
from difflib import SequenceMatcher

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

    # A named entity status question such as "what is the status of chatbot test 11"
    # should search projects even when the user omits the word "project". Explicit
    # task/issue/milestone wording still takes precedence and is not routed here.
    implicit_project_status = bool(re.search(
        r"\bstatus\s+(?:of|for)\s+(?!my\b|all\b|the\s+project\b)[a-z0-9][a-z0-9 _.-]*[a-z0-9]\s*[?.!]*$",
        text,
    )) and not has((r"\btasks?\b", r"\bissues?\b", r"\bdefects?\b", r"\bmilestones?\b"))

    wants_projects = has((
        r"\bprojects?\b",
        r"\bproject\s+(?:status|manager|owner|team|members|priority|details?)\b",
    )) or implicit_project_status

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


def _identity_matches(candidate, identities, allow_fuzzy=True):
    """Match IDs exactly and person names by token or >=90% similarity.

    This supports a short query such as "Prasanthi" matching the stored full
    name "Poli Prasanthi", and small spelling differences in person names.
    Numeric IDs remain exact-only so fuzzy matching cannot cross-match records.
    """
    candidate_text = _normal_text(candidate)
    if not candidate_text:
        return False

    def name_form(value):
        # Ignore punctuation and repeated whitespace for display-name comparison.
        return " ".join(re.findall(r"[a-z0-9]+", _normal_text(value)))

    normalized_identities = {
        _normal_text(value) for value in identities if _normal_text(value)
    }
    if candidate_text in normalized_identities:
        return True

    if candidate_text.isdigit():
        return False

    candidate_name = name_form(candidate_text)
    candidate_tokens = set(candidate_name.split())

    for identity in normalized_identities:
        # Never use fuzzy matching for numeric IDs.
        if identity.isdigit():
            continue
        identity_name = name_form(identity)
        if not identity_name:
            continue

        # A first/last name token matches a full display name, e.g.
        # "Prasanthi" -> "Poli Prasanthi".
        if identity_name in candidate_tokens or candidate_name in set(identity_name.split()):
            return True

        # Also allow a full-name substring, but only on word boundaries.
        if re.search(rf"(?<![a-z0-9]){re.escape(identity_name)}(?![a-z0-9])", candidate_name):
            return True

        # Exact token/full-name matches always take precedence globally in
        # the caller. Fuzzy matching is only a fallback when no exact person
        # match exists in the result set.
        if not allow_fuzzy:
            continue
        if SequenceMatcher(None, candidate_name, identity_name).ratio() >= 0.82:
            return True
        identity_tokens = identity_name.split()
        if any(
            len(candidate_name) >= 5
            and len(token) >= 5
            and SequenceMatcher(None, candidate_name, token).ratio() >= 0.78
            for token in identity_tokens
        ):
            return True
        if any(
            len(candidate_token) >= 5
            and len(token) >= 5
            and SequenceMatcher(None, candidate_token, token).ratio() >= 0.78
            for candidate_token in candidate_tokens
            for token in identity_tokens
        ):
            return True

    return False


def _normalize_field_name(value):
    """Normalize snake_case, camelCase, and spaced API field names."""
    value = str(value or "")
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return re.sub(r"[-\s]+", "_", value).lower()


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


def _record_assignment_matches(record, entity_type, identities, include_owner=False, allow_fuzzy=True):
    if not isinstance(record, dict) or not identities:
        return False
    common = {
        "task": {"assignee", "assignees", "assignee_name", "assignee_names", "assignee_id", "assignee_ids", "assignee_email", "assignee_emails", "assigned_to", "assigned_to_id", "assigned_to_name", "assigned_user", "assigned_users", "task_owner", "task_owners", "task_owner_name", "task_owner_names", "task_owner_full_name", "task_owner_display_name", "task_owner_id", "task_owner_ids", "assignee_full_name", "assignee_display_name", "assigned_to_display_name", "user"},
        "issue": {"assignee", "assignees", "assignee_name", "assignee_id", "assignee_email", "assignee_emails", "assigned_to", "assigned_to_id", "assigned_user", "reporter", "reporter_name", "created_by"},
        # For "my projects" / "projects assigned to me", membership means
        # the authenticated user appears in the project team-member data.
        # Ownership or manager status alone is not team membership.
        "project": {"team", "team_members", "team_member", "team_member_ids", "team_members_ids", "project_members", "project_member", "members", "member", "users", "user_emails", "team_emails", "team_member_names", "team_member_emails", "manager_name", "project_manager_name", "delivery_head_name", "project_manager", "delivery_head"},
        "milestone": {"assignee", "assignees", "owner", "owner_name", "assigned_to", "user", "users"},
        "tasklist": {"assignee", "assignees", "owner", "owner_name", "assigned_to", "user", "users"},
        "timelog": {"user", "user_name", "username", "user_id", "logged_by", "logged_by_name", "employee", "employee_name", "created_by", "created_by_name"},
    }.get(entity_type, set())
    if include_owner and entity_type == "task":
        common = common | {
            "owner", "owners", "owner_name", "owner_names",
            "created_by", "creator", "creator_name",
        }
    # Also recognize explicit assignment-related fields without matching unrelated project/task text.
    for key, value in _walk_values(record):
        key_norm = _normalize_field_name(key)
        relevant = key_norm in common or any(token in key_norm for token in ("assignee", "assigned_to", "task_owner", "team_member", "user_email"))
        if entity_type == "project":
            # "My projects" specifically means team membership, not ownership
            # or manager status by itself.
            relevant = (
                key_norm in common
                or "team_member" in key_norm
                or key_norm in {
                    "team", "members", "member", "users", "project_members",
                    "project_member", "user_emails", "team_emails",
                    "manager_name", "project_manager_name", "delivery_head_name",
                    "project_manager", "delivery_head",
                }
            )
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
            candidates = [v for k, v in value.items() if _normalize_field_name(k) in {"id", "user_id", "name", "full_name", "first_name", "last_name", "username", "email", "email_address"} and not isinstance(v, (dict, list))]
            first = value.get("first_name") or value.get("firstName")
            last = value.get("last_name") or value.get("lastName")
            if first or last:
                candidates.append(f"{first or ''} {last or ''}")
        for candidate in candidates:
            if isinstance(candidate, dict):
                for k, v in candidate.items():
                    if _normalize_field_name(k) in {"id", "user_id", "name", "full_name", "username", "email", "first_name", "last_name"} and v is not None:
                        if _identity_matches(v, identities, allow_fuzzy=allow_fuzzy):
                            return True
                first = candidate.get("first_name") or candidate.get("firstName")
                last = candidate.get("last_name") or candidate.get("lastName")
                if (first or last) and _identity_matches(f"{first or ''} {last or ''}", identities, allow_fuzzy=allow_fuzzy):
                    return True
            elif _identity_matches(candidate, identities, allow_fuzzy=allow_fuzzy):
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


def _filter_records_for_current_user(payload, entity_type, identities, include_owner=False):
    records, parent, key = _extract_record_list(payload)
    if records is None:
        return payload, None
    filtered = [r for r in records if _record_assignment_matches(r, entity_type, identities, include_owner=include_owner)]
    if parent is None:
        return filtered, len(filtered)
    parent[key] = filtered
    # Keep count metadata consistent with the filtered records. APIs may use
    # different naming conventions for project totals.
    count_keys = (
        "total", "total_count", "totalCount", "filtered_count", "filteredCount",
        "count", "total_projects", "totalProjects", "projects_count", "projectsCount",
        "total_records", "totalRecords", "total_items", "totalItems",
    )
    for count_key in count_keys:
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




# ============================================================
# DETERMINISTIC FIELD/VALUE FILTERING
# ============================================================

_STATUS_VALUES = (
    "in progress", "inprogress", "in_progress", "in-progress",
    "completed", "complete", "closed", "open", "active", "inactive",
    "pending", "on hold", "onhold", "on_hold", "planning", "planned",
    "to do", "todo", "new", "blocked", "resolved", "reopened",
    "cancelled", "canceled", "overdue", "in review", "inreview",
)
_PRIORITY_VALUES = ("critical", "urgent", "highest", "high", "medium", "normal", "low", "lowest")

_FILTER_FIELD_ALIASES = {
    "status": (
        "status", "status_name", "statusName", "task_status", "taskStatus",
        "project_status", "projectStatus", "issue_status", "issueStatus",
        "state", "state_name", "stateName",
    ),
    "priority": (
        "priority", "priority_name", "priorityName", "priority_label",
        "priorityLabel", "task_priority", "taskPriority", "project_priority",
        "projectPriority", "severity", "severity_name", "severityName",
    ),
    "billing model": ("billing_model", "billingModel", "billing_type", "billingType", "billing"),
    "project type": ("project_type", "projectType", "type", "type_name", "typeName"),
    "completion": (
        "completion", "completion_percentage", "completionPercentage", "progress",
        "progress_percentage", "progressPercentage", "percent_complete", "percentComplete",
    ),
    "due date": ("due_date", "dueDate", "deadline", "expected_end_date", "expectedEndDate", "end_date", "endDate", "target_date", "targetDate"),
    "start date": ("start_date", "startDate", "planned_start_date", "plannedStartDate", "expected_start_date", "expectedStartDate"),
    "created date": ("created_at", "createdAt", "created_date", "createdDate", "creation_date", "creationDate"),
    "updated date": ("updated_at", "updatedAt", "modified_at", "modifiedAt", "updated_date", "updatedDate"),
    "owner": ("owner", "owner_name", "ownerName", "task_owner", "task_owner_name", "taskOwner", "taskOwnerName", "project_owner", "projectOwner"),
    "assignee": ("assignee", "assignees", "assignee_name", "assigneeName", "assigned_to", "assignedTo", "assigned_user", "assignedUser", "assigned_users", "assignedUsers"),
    "manager": ("manager", "manager_name", "managerName", "project_manager", "projectManager", "project_manager_name", "projectManagerName"),
    "severity": ("severity", "severity_name", "severityName"),
}

_FILTER_DISPLAY_LABELS = {
    "status": "Status",
    "priority": "Priority",
    "billing model": "Billing Model",
    "project type": "Project Type",
    "completion": "Completion",
    "due date": "Due Date",
    "start date": "Start Date",
    "created date": "Created Date",
    "updated date": "Updated Date",
    "owner": "Owner",
    "assignee": "Assignee",
    "manager": "Manager",
    "severity": "Severity",
}


def _canonical_filter_value(value):
    """Normalize label values so 'In Progress', 'inprogress' and 'IN_PROGRESS' match."""
    if isinstance(value, dict):
        value = next((value.get(k) for k in (
            "label", "name", "value", "display_name", "displayName",
            "status_name", "statusName", "priority_name", "priorityName",
        ) if value.get(k) not in (None, "")), None)
    if isinstance(value, list):
        return [_canonical_filter_value(item) for item in value]
    if value is None:
        return ""
    return re.sub(r"[^a-z0-9]+", "", str(value).casefold())


def _detect_attribute_filter(user_message):
    """Recognize common field=value filters in natural language.

    Only explicit field/value requests are returned. Sorting requests are never
    interpreted as filters (e.g. 'sort by priority').
    """
    text = _normal_text(user_message)
    if not text or _detect_sort_request(text):
        return None

    # Match known status values first, longest first, including no-space variants.
    status_values = sorted(_STATUS_VALUES, key=len, reverse=True)
    status_alt = "|".join(re.escape(v).replace(r"\ ", r"[\s_-]*") for v in status_values)
    status_patterns = (
        rf"\b(?:status|state)\s*(?:is|as|=|equals?|equal\s+to|of)?\s*({status_alt})\b",
        rf"\b(?:with|whose|having)\s+(?:a\s+)?(?:status|state)\s*(?:is|as|=)?\s*({status_alt})\b",
    )
    for pattern in status_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return {"field": "status", "value": match.group(1), "operator": "equals"}

    priority_alt = "|".join(re.escape(v) for v in sorted(_PRIORITY_VALUES, key=len, reverse=True))
    priority_patterns = (
        # "priority medium", "priority is medium", "priority as medium"
        rf"\bpriority\s*(?:is|as|=|equals?|equal\s+to)?\s*({priority_alt})\b",
        # "medium priority" and the common conversational form "medium as priority"
        rf"\b({priority_alt})\s+(?:as\s+)?priority\b",
    )
    for pattern in priority_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return {"field": "priority", "value": match.group(1), "operator": "equals"}

    # Common explicit field/value forms beyond status and priority.
    generic_fields = {
        "billing model": r"\b(?:billing\s+model|billing\s+type|billing)\b",
        "project type": r"\bproject\s+type\b",
    }
    for field, field_pattern in generic_fields.items():
        match = re.search(
            field_pattern + r"\s*(?:is|as|=|equals?|equal\s+to)\s*([a-z0-9][a-z0-9 &/_-]*?)(?=\s+\b(?:and|or|with|where|whose|having|sort|order)\b|[?.!,;]|$)",
            text,
            re.IGNORECASE,
        )
        if match:
            value = match.group(1).strip(" _-/")
            if value:
                return {"field": field, "value": value, "operator": "equals"}

    # Date comparisons: due date before/after a specific date.
    date_filter = re.search(
        r"\b(due\s+date|deadline|start\s+date|created\s+date|updated\s+date)\s*"
        r"(before|after|on|is|=)\s*(\d{4}-\d{2}-\d{2})\b",
        text,
        re.IGNORECASE,
    )
    if date_filter:
        field_text, op_text, date_value = date_filter.groups()
        field_text = re.sub(r"\s+", " ", field_text.casefold())
        field = "due date" if field_text == "deadline" else field_text
        operator = {"before": "lt", "after": "gt", "on": "eq", "is": "eq", "=": "eq"}[op_text.casefold()]
        return {"field": field, "value": date_value, "operator": operator}

    # Explicit equality filters for common fields. This allows the same
    # field=value workflow to cover dates, owners, assignees, managers, and
    # other record attributes, not only status/priority.
    generic_field_patterns = (
        ("due date", r"\b(?:due\s+date|deadline)\b"),
        ("start date", r"\bstart\s+date\b"),
        ("created date", r"\bcreated\s+date\b"),
        ("updated date", r"\bupdated\s+date\b"),
        ("owner", r"\b(?:task\s+owner|project\s+owner|owner)\b"),
        ("assignee", r"\b(?:assignee|assigned\s+user)\b"),
        ("manager", r"\b(?:project\s+manager|manager)\b"),
        ("severity", r"\bseverity\b"),
    )
    for field, field_pattern in generic_field_patterns:
        match = re.search(
            field_pattern + r"\s*(?:is|as|=|equals?|equal\s+to)\s*"
            r"([a-z0-9][a-z0-9 @._/-]*?)(?=\s+\b(?:and|or|with|where|whose|having|sort|order)\b|[?.!,;]|$)",
            text,
            re.IGNORECASE,
        )
        if match:
            value = match.group(1).strip(" _-/")
            if value:
                return {"field": field, "value": value, "operator": "equals"}

    # Numeric completion/progress filters: e.g. completion above 50%.
    progress = re.search(
        r"\b(?:completion|progress)\s*(above|over|greater\s+than|more\s+than|at\s+least|below|under|less\s+than|at\s+most|=|is)\s*(\d+(?:\.\d+)?)\s*%?",
        text,
        re.IGNORECASE,
    )
    if progress:
        op_text, number = progress.groups()
        op_text = re.sub(r"\s+", " ", op_text.casefold())
        operator = {
            "above": "gt", "over": "gt", "greater than": "gt", "more than": "gt",
            "at least": "gte", "below": "lt", "under": "lt", "less than": "lt",
            "at most": "lte", "=": "eq", "is": "eq",
        }[op_text]
        return {"field": "completion", "value": float(number), "operator": operator}

    return None


def _field_values_for_record(record, field):
    """Extract real field values from flat or nested API records."""
    if not isinstance(record, dict):
        return []
    aliases = {_normalize_field_name(k) for k in _FILTER_FIELD_ALIASES.get(field, ())}
    found = []

    # Prefer direct entity fields. For task records, do not accidentally read
    # status/priority from the nested parent project.
    for key, value in record.items():
        normalized = _normalize_field_name(key)
        if normalized in aliases and value not in (None, ""):
            found.append(value)

    def walk(value, inside_project=False):
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = _normalize_field_name(key)
                project_context = inside_project or normalized in {
                    "project", "project_details", "project_record", "parent_project"
                }
                if normalized in aliases and child not in (None, "") and not project_context:
                    found.append(child)
                if isinstance(child, (dict, list)):
                    walk(child, project_context)
        elif isinstance(value, list):
            for item in value:
                walk(item, inside_project)

    walk(record)
    # De-duplicate by canonical representation.
    unique = []
    seen = set()
    for value in found:
        canonical = _canonical_filter_value(value)
        if canonical and canonical not in seen:
            seen.add(canonical)
            unique.append(value)
    return unique


def _record_matches_attribute_filter(record, filter_spec):
    field = filter_spec["field"]
    wanted = filter_spec["value"]
    operator = filter_spec.get("operator", "equals")
    values = _field_values_for_record(record, field)
    if not values:
        return False
    if field == "completion":
        try:
            target = float(wanted)
        except (TypeError, ValueError):
            return False
        for raw in values:
            if isinstance(raw, dict):
                raw = next((raw.get(k) for k in ("value", "label", "name", "percentage") if raw.get(k) not in (None, "")), None)
            match = re.search(r"-?\d+(?:\.\d+)?", str(raw or "").replace(",", ""))
            if not match:
                continue
            actual = float(match.group())
            if (
                (operator == "gt" and actual > target)
                or (operator == "gte" and actual >= target)
                or (operator == "lt" and actual < target)
                or (operator == "lte" and actual <= target)
                or (operator == "eq" and actual == target)
            ):
                return True
        return False
    if field in {"due date", "start date", "created date", "updated date"} and operator in {"gt", "lt", "eq"}:
        target_date = str(wanted)[:10]
        for raw in values:
            actual_date = _display_field_value(raw)[:10]
            if not actual_date:
                continue
            if (
                (operator == "gt" and actual_date > target_date)
                or (operator == "lt" and actual_date < target_date)
                or (operator == "eq" and actual_date == target_date)
            ):
                return True
        return False
    wanted_normalized = _canonical_filter_value(wanted)
    return any(_canonical_filter_value(value) == wanted_normalized for value in values)


def _apply_attribute_filter_to_messages(messages, user_message):
    """Filter current-turn authorized tool payloads before LLM or HTML rendering."""
    filter_spec = _detect_attribute_filter(user_message)
    if not filter_spec:
        return messages, None, False

    # Determine the requested entity from the query so unrelated tool payloads
    # are not modified if multiple read tools were called.
    text = _normal_text(user_message)
    entity_patterns = (
        ("tasklist", r"\btask\s*lists?\b|\btasklists?\b"),
        ("task", r"\btasks?\b"),
        ("project", r"\bprojects?\b"),
        ("issue", r"\bissues?\b|\bdefects?\b"),
        ("milestone", r"\bmilestones?\b"),
        ("timelog", r"\btime\s*logs?\b|\btimesheets?\b|\btime\s*entries\b"),
    )
    requested_entity = next((entity for entity, pattern in entity_patterns if re.search(pattern, text)), None)
    tool_to_entity = {
        "get_my_projects": "project", "get_my_tasks": "task",
        "get_my_issues": "issue", "get_my_milestones": "milestone",
        "get_my_tasklists": "tasklist", "get_my_timelogs": "timelog",
    }
    if requested_entity is None:
        return messages, filter_spec, False

    # Map current-turn tool call IDs to the actual tool names.
    current_user_index = max(
        (i for i, message in enumerate(messages) if message.get("role") == "user"),
        default=-1,
    )
    turn_start = current_user_index + 1 if current_user_index >= 0 else 0
    turn = messages[turn_start:]
    tool_names = {}
    for message in turn:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls", []) or []:
                function = call.get("function", {}) or {}
                tool_names[call.get("id")] = function.get("name")

    updated_messages = [dict(message) for message in messages]
    applied = False
    for idx in range(turn_start, len(updated_messages)):
        message = updated_messages[idx]
        if message.get("role") != "tool":
            continue
        tool_name = tool_names.get(message.get("tool_call_id")) or message.get("name") or message.get("tool_name")
        if tool_to_entity.get(tool_name) != requested_entity:
            continue
        try:
            payload = json.loads(message.get("content") or "null")
        except (TypeError, json.JSONDecodeError):
            continue
        records, parent, key = _extract_record_list(payload)
        if records is None:
            continue

        valid_records = [record for record in records if isinstance(record, dict)]
        original_record_count = len(valid_records)
        # A result is considered complete when the API's authoritative total
        # equals the returned record count, or when no total is provided and
        # the tool returned more than the normal five-record display limit.
        original_total = None
        count_keys_for_read = (
            "total", "total_count", "totalCount", "count", "total_records",
            "totalRecords", "total_items", "totalItems", "total_projects",
            "totalProjects", "total_tasks", "totalTasks",
        )
        for source in (parent, payload):
            if isinstance(source, dict):
                for count_key in count_keys_for_read:
                    count_value = source.get(count_key)
                    if isinstance(count_value, int) and not isinstance(count_value, bool) and count_value >= 0:
                        original_total = count_value
                        break
            if original_total is not None:
                break
        complete_candidate_set = (
            (original_total is not None and original_total == original_record_count)
            or (original_total is None and original_record_count > 5)
        )

        values_present = any(_field_values_for_record(record, filter_spec["field"]) for record in valid_records)
        filtered_records = [
            record for record in valid_records
            if _record_matches_attribute_filter(record, filter_spec)
        ]

        if not values_present and valid_records:
            if isinstance(payload, dict):
                payload["_chatbot_filter_error"] = (
                    f"The {filter_spec['field']} field was not present in the authorized {requested_entity} records."
                )
                # Never show unfiltered data when the requested field cannot be verified.
                if parent is None:
                    payload = {"items": [], "_chatbot_filter_error": (
                        f"The {filter_spec['field']} field was not present in the authorized {requested_entity} records."
                    )}
                else:
                    parent[key] = []
        else:
            if parent is None:
                payload = filtered_records
            else:
                parent[key] = filtered_records

        # Keep counts tied to matching records. If the API returned the complete
        # candidate set, filtered count is authoritative. If the API returned only
        # one page of a larger dataset, do not invent a global filtered total.
        metadata_sources = [payload]
        if isinstance(parent, dict):
            metadata_sources.insert(0, parent)
        count_keys = {
            "total", "total_count", "totalCount", "count", "total_records",
            "totalRecords", "total_items", "totalItems", "total_projects",
            "totalProjects", "total_tasks", "totalTasks", "project_count",
            "projectCount", "task_count", "taskCount",
        }
        if isinstance(payload, dict):
            payload["filtered_count"] = len(filtered_records)
        for source in metadata_sources:
            if not isinstance(source, dict):
                continue
            if complete_candidate_set:
                for count_key in count_keys:
                    if count_key in source and isinstance(source[count_key], (int, float)):
                        source[count_key] = len(filtered_records)
                for more_key in ("has_more", "hasMore", "has_next", "hasNext"):
                    if more_key in source:
                        source[more_key] = len(filtered_records) > 5
            else:
                # The API page is incomplete: its total describes unfiltered data.
                # Remove that total so the renderer will use a plain View More link
                # rather than show a false remaining-match count.
                for count_key in count_keys:
                    source.pop(count_key, None)
        message["content"] = json.dumps(payload, ensure_ascii=False, default=str)
        applied = True
        logger.info(
            "Applied deterministic PMS field filter (entity=%s field=%s value=%r input=%s output=%s)",
            requested_entity, filter_spec["field"], filter_spec["value"],
            len(valid_records), len(filtered_records),
        )

    return updated_messages, filter_spec, applied




def _display_field_value(value):
    if isinstance(value, dict):
        value = next((value.get(k) for k in (
            "label", "name", "value", "display_name", "displayName",
            "status_name", "statusName", "priority_name", "priorityName",
        ) if value.get(k) not in (None, "")), None)
    if isinstance(value, list):
        return ", ".join(str(_display_field_value(item)) for item in value if item not in (None, ""))
    return "" if value is None else str(value)


def _safe_html_fallback(messages, user_message=""):
    """Render authorized current-turn tool results if final LLM formatting fails.

    This is deliberately a presentation fallback only: it uses tool results,
    preserves their order unless reliable dates are available, displays at most
    five records, and uses server-provided pagination metadata when present.
    """
    current_user_index = max(
        (i for i, message in enumerate(messages) if message.get("role") == "user"),
        default=-1,
    )
    turn = messages[current_user_index + 1:] if current_user_index >= 0 else messages

    tool_names = {}
    for message in turn:
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls", []) or []:
            function = call.get("function", {}) or {}
            tool_names[call.get("id")] = function.get("name")

    chunks = []
    seen_entities = set()
    filter_spec = _detect_attribute_filter(user_message)
    sort_spec = _detect_sort_request(user_message)

    for message in turn:
        if message.get("role") != "tool":
            continue

        tool_name = tool_names.get(message.get("tool_call_id")) or message.get("name") or message.get("tool_name")
        meta = ENTITY_META.get(tool_name)
        if not meta:
            continue

        entity_type, route, name_keys = meta
        # Task lists are shown on the existing Tasks page.
        if entity_type == "tasklist":
            route = "tasks"

        try:
            payload = json.loads(message.get("content") or "null")
        except (TypeError, json.JSONDecodeError):
            continue

        if isinstance(payload, dict) and payload.get("_chatbot_filter_error"):
            chunks.append(
                f"<p>I couldn't apply the requested filter because "
                f"{html.escape(str(payload.get('_chatbot_filter_error')))}</p>"
            )
            continue

        if isinstance(payload, dict) and payload.get("error"):
            chunks.append(
                "<p>I couldn't retrieve the requested records right now. Please try again.</p>"
            )
            continue

        if isinstance(payload, dict) and payload.get("clarification"):
            question = html.escape(str(payload.get("clarification")))
            chunks.append(f"<p>{question}</p>")
            choices = payload.get("available_projects") or []
            if choices:
                chunks.append("<p>Here are the project names I can see:</p><ul>")
                for choice in choices:
                    chunks.append(f"<li>{html.escape(str(choice))}</li>")
                chunks.append("</ul>")
            continue

        records, parent, _ = _extract_record_list(payload)
        if records is None:
            continue

        # DEFENSIVE NAMED-PERSON FILTER: never let the LLM decide whether a
        # requested person's tasks exist. The read tool may return authorized
        # records correctly while the final LLM still hallucinates an empty set.
        # Match names against assignee fields using the same token/fuzzy matcher
        # used by the post-tool filter (e.g. "prashanthi" -> "Poli Prasanthi").
        requested_person = _extract_requested_person(user_message)
        person_filter_applies = False
        if requested_person:
            query_text = _normal_text(user_message)
            requested_entity_for_person = None
            for candidate_entity, pattern in (
                ("tasklist", r"\btask\s*lists?\b|\btasklists?\b"),
                ("task", r"\btasks?\b"),
                ("project", r"\bprojects?\b"),
                ("issue", r"\bissues?\b|\bdefects?\b"),
                ("milestone", r"\bmilestones?\b"),
                ("timelog", r"\btime\s*logs?\b|\btimesheets?\b|\btime\s*entries\b"),
            ):
                if re.search(pattern, query_text):
                    requested_entity_for_person = candidate_entity
                    break

            if requested_entity_for_person and requested_entity_for_person == entity_type:
                explicit_owner_query = bool(re.search(
                    r"\b(?:owned by|owner is|whose owner is)\b",
                    query_text,
                ))
                include_owner = entity_type != "task" or explicit_owner_query
                person_identities = {_normal_text(requested_person)}
                # First search for exact full-name/token matches across ALL
                # returned records. Only use fuzzy spelling matching if no
                # exact match exists, preventing "Manikandan" from selecting
                # a different person such as "Balaaji Manivaran" when an
                # exact Manikandan record is present.
                exact_records = [
                    record for record in records
                    if isinstance(record, dict) and _record_assignment_matches(
                        record, entity_type, person_identities,
                        include_owner=include_owner, allow_fuzzy=False
                    )
                ]
                if exact_records:
                    records = exact_records
                    match_mode = "exact"
                else:
                    records = [
                        record for record in records
                        if isinstance(record, dict) and _record_assignment_matches(
                            record, entity_type, person_identities,
                            include_owner=include_owner, allow_fuzzy=True
                        )
                    ]
                    match_mode = "fuzzy_fallback"
                person_filter_applies = True
                logger.info(
                    "Applied deterministic final named-person filter "
                    "(entity=%s, person=%s, mode=%s, matched=%s)",
                    entity_type, requested_person, match_mode, len(records),
                )

        # DEFENSIVE FILTER: filter the records again at the final HTML boundary.
        # This prevents an unfiltered LLM/tool response from leaking into the
        # visible answer if an earlier message-rewrite step could not identify
        # the tool-call ID. Do not trust the LLM to perform a requested filter.
        filter_applies_to_entity = False
        filter_error = None
        if filter_spec:
            query_text = _normal_text(user_message)
            requested_entity = None
            for candidate_entity, pattern in (
                ("tasklist", r"\btask\s*lists?\b|\btasklists?\b"),
                ("task", r"\btasks?\b"),
                ("project", r"\bprojects?\b"),
                ("issue", r"\bissues?\b|\bdefects?\b"),
                ("milestone", r"\bmilestones?\b"),
                ("timelog", r"\btime\s*logs?\b|\btimesheets?\b|\btime\s*entries\b"),
            ):
                if re.search(pattern, query_text):
                    requested_entity = candidate_entity
                    break
            if requested_entity and entity_type != requested_entity:
                # A task filter must never accidentally render projects (or vice versa).
                continue
            filter_applies_to_entity = requested_entity == entity_type
            if filter_applies_to_entity:
                source_records = [record for record in records if isinstance(record, dict)]
                field_values_present = any(
                    _field_values_for_record(record, filter_spec["field"])
                    for record in source_records
                )
                if source_records and not field_values_present:
                    filter_error = (
                        f"the {filter_spec['field']} field is not present in the returned "
                        f"{entity_type} records, so I can't verify the filter"
                    )
                    records = []
                else:
                    records = [
                        record for record in source_records
                        if _record_matches_attribute_filter(record, filter_spec)
                    ]

        # Avoid rendering the same tool/entity twice if the graph contains a
        # repeated tool message for the same entity type.
        if entity_type in seen_entities:
            continue
        seen_entities.add(entity_type)

        # Prefer explicit, server-provided totals/has_more. A page length is
        # not a reliable total when the API has already paginated the results.
        metadata_sources = [payload]
        if isinstance(parent, dict) and parent is not payload:
            metadata_sources.insert(0, parent)

        total_count = None
        has_more = False
        # When this fallback itself applied a filter, original API totals describe
        # the unfiltered collection and must not be reused as filtered counts.
        if filter_applies_to_entity or person_filter_applies:
            # The local filter ran against the complete records returned for this
            # turn, so original API totals refer to the unfiltered set and cannot
            # be reused for the filtered result.
            raw_count = len(records)
            total_count = raw_count
            has_more = raw_count > 5
        for source in (() if (filter_applies_to_entity or person_filter_applies) else metadata_sources):
            if not isinstance(source, dict):
                continue
            # Support common total-count names used by task and project APIs.
            # Never guess a total from a paginated page size.
            for count_key in (
                "total_count", "totalCount", "total", "filtered_count", "filteredCount",
                "count", "total_projects", "totalProjects", "projects_count", "projectsCount",
                "total_records", "totalRecords", "total_items", "totalItems",
                "project_count", "projectCount", "task_count", "taskCount",
            ):
                count_value = source.get(count_key)
                if isinstance(count_value, int) and not isinstance(count_value, bool) and count_value >= 0:
                    total_count = count_value
                    break
            if (
                source.get("has_more") is True
                or source.get("hasMore") is True
                or source.get("has_next") is True
                or source.get("hasNext") is True
            ):
                has_more = True
            if source.get("has_more") is False or source.get("hasMore") is False:
                # An explicit false is authoritative unless a later source
                # provides a true value.
                has_more = False
            if total_count is not None:
                break

        if sort_spec:
            sort_field, sort_reverse = sort_spec
            valid_records = [record for record in records if isinstance(record, dict)]
            present = [record for record in valid_records if _sort_value(record, sort_field)[0] == 0]
            missing = [record for record in valid_records if _sort_value(record, sort_field)[0] != 0]
            present.sort(key=lambda record: _sort_value(record, sort_field), reverse=sort_reverse)
            ordered_records = present + missing
        else:
            dated = any(_record_date(record) for record in records)
            ordered_records = sorted(records, key=_record_date, reverse=True) if dated else list(records)
        shown = ordered_records[:5]

        title = "time logs" if entity_type == "timelog" else route.replace("-", " ")
        if filter_error:
            chunks.append(f"<p>I couldn't apply the requested filter because {html.escape(filter_error)}.</p>")
            continue
        if not shown:
            if filter_applies_to_entity:
                chunks.append(
                    f"<p>No {html.escape(title)} matched "
                    f"{html.escape(_FILTER_DISPLAY_LABELS.get(filter_spec['field'], filter_spec['field'].title()))}: "
                    f"{html.escape(str(filter_spec['value']))}.</p>"
                )
            else:
                chunks.append(f"<p>No matching {html.escape(title)} found.</p>")
            continue

        if person_filter_applies and requested_person and not filter_spec:
            chunks.append(
                f"<p>Here are the {html.escape(title)} assigned to "
                f"{html.escape(requested_person.title())}:</p><ol>"
            )
        elif filter_spec:
            filter_label = _FILTER_DISPLAY_LABELS.get(filter_spec["field"], filter_spec["field"].title())
            filter_value = html.escape(str(filter_spec["value"]))
            chunks.append(
                f"<p>Here are the {html.escape(title)} with "
                f"{html.escape(filter_label)}: {filter_value}:</p><ol>"
            )
        else:
            chunks.append(f"<p>Here are the {html.escape(title)}:</p><ol>")
        for record in shown:
            name = next(
                (record.get(key) for key in name_keys if record.get(key) not in (None, "")),
                None,
            )
            if name is None:
                singular = title[:-1].title() if title.endswith("s") else title.title()
                name = f"{singular} record"

            record_id = next(
                (record.get(key) for key in ("id", "pk") if record.get(key) not in (None, "")),
                None,
            )
            public_id = next(
                (record.get(key) for key in ("public_id", "publicId", "task_id", "project_id_sync", "issue_id") if record.get(key) not in (None, "")),
                None,
            )

            name_html = html.escape(str(name))
            navigation_url = record.get("navigation_url") or record.get("navigationUrl")
            if isinstance(navigation_url, str) and navigation_url.startswith("/") and not navigation_url.startswith("//"):
                href = html.escape(navigation_url, quote=True)
            elif record_id is not None:
                href = f"/{route}/{html.escape(str(record_id), quote=True)}"
            else:
                href = None
            if href:
                name_html = f'<a href="{href}">{name_html}</a>'

            suffix = f" ({html.escape(str(public_id))})" if public_id is not None else ""
            project_name = next(
                (record.get(key) for key in ("project_name", "projectName") if record.get(key) not in (None, "")),
                None,
            )
            project_id = next(
                (record.get(key) for key in ("project_id", "projectId") if record.get(key) not in (None, "")),
                None,
            )
            project_suffix = ""
            if entity_type != "project" and project_name:
                safe_project_name = html.escape(str(project_name))
                if project_id is not None:
                    safe_project_name = (
                        f'<a href="/projects/{html.escape(str(project_id), quote=True)}">'
                        f"{safe_project_name}</a>"
                    )
                project_suffix = f" — Project: {safe_project_name}"

            requested_field_suffix = ""
            display_field = filter_spec["field"] if filter_spec else (sort_spec[0] if sort_spec else None)
            if display_field:
                field_label = _FILTER_DISPLAY_LABELS.get(display_field, display_field.title())
                actual_values = _field_values_for_record(record, display_field)
                if not actual_values:
                    # Sorting-only fields such as name/budget may be outside the filter aliases.
                    raw_sort_value = _sort_value(record, display_field) if sort_spec else None
                    actual_value = None if not raw_sort_value or raw_sort_value[0] != 0 else raw_sort_value[-1]
                else:
                    actual_value = _display_field_value(actual_values[0])
                actual_text = html.escape(str(actual_value)) if actual_value not in (None, "") else "value unavailable"
                requested_field_suffix = f" — {html.escape(field_label)}: {actual_text}"

            chunks.append(f"<li>{name_html}{suffix}{project_suffix}{requested_field_suffix}</li>")
        chunks.append("</ol>")

        # Only claim more results when the API explicitly confirms them, or
        # when an authoritative total is greater than the number displayed.
        # More than five returned records is itself proof that more can be viewed.
        more_available = (
            has_more
            or (total_count is not None and total_count > len(shown))
            or len(records) > len(shown)
        )
        if more_available:
            if total_count is not None:
                remaining = max(total_count - len(shown), 0)
                label = f"View More ({remaining:,} more {title})" if remaining else f"View more {title}"
            else:
                label = f"View more {title}"
            chunks.append(f'<p><a href="/{route}">{html.escape(label)}</a></p>')

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
    """Return True when the user asks for their own assigned work.

    In task queries, 'my tasks', 'tasks for me', and 'tasks assigned to me'
    all mean tasks where the authenticated user is an assignee. Ownership or
    task creation alone must not make a task part of this result.
    """
    text = _normal_text(message)
    patterns = (
        r"\b(?:assigned|allocated|delegated|given)\s+to\s+me\b",
        r"\b(?:assigned|allocated|delegated|given)\s+me\b",
        r"\bmy\s+(?:assigned\s+)?(?:tasks?|projects?|issues?|defects?|milestones?|task\s*lists?|time\s*logs?)\b",
        r"\b(?:tasks?|projects?|issues?|defects?|milestones?|task\s*lists?|time\s*logs?)\s+(?:assigned|allocated|delegated|given)\s+to\s+me\b",
        r"\b(?:tasks?|projects?|issues?)\s+for\s+me\b",
        r"\bwhat\s+(?:are|is)\s+my\s+(?:tasks?|projects?|issues?)\b",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def _extract_requested_person(message: str) -> str | None:
    """Extract a named person only when the query explicitly relates records to them.

    This is used after the read tool returns its access-controlled records.
    It intentionally does not treat generic all-record requests as person filters.
    """
    text = _normal_text(message)
    patterns = (
        # "tasks assigned to Prasanthi", "projects managed by Yasmin"
        r"\b(?:assigned|allocated|delegated|given)\s+to\s+(?!me\b)([a-z][a-z .'-]*?)(?=\s+(?:and|with|in|under|for|from|whose|that|which)\b|[?.!,;]|$)",
        r"\b(?:owned|created|managed|led)\s+by\s+([a-z][a-z .'-]*?)(?=\s+(?:and|with|in|under|for|from|whose|that|which)\b|[?.!,;]|$)",
        # "Prasanthi's projects/tasks", "projects of Prasanthi", "tasks for Prasanthi"
        r"\b([a-z][a-z .'-]*?)['’]s\s+(?:all\s+)?(?:projects?|tasks?|issues?|defects?|milestones?|task\s*lists?|time\s*logs?)\b",
        r"\b(?:projects?|tasks?|issues?|defects?|milestones?|task\s*lists?|time\s*logs?)\s+(?:of|for|belonging\s+to)\s+([a-z][a-z .'-]*?)(?=[?.!,;]|$)",
        r"\b(?:show|list|get|find)\s+(?:me\s+)?(?:the\s+)?(?:projects?|tasks?|issues?|defects?|milestones?|task\s*lists?|time\s*logs?)\s+(?:assigned\s+to|owned\s+by|created\s+by|managed\s+by)\s+([a-z][a-z .'-]*?)(?=[?.!,;]|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        person = re.sub(r"\s+", " ", match.group(1)).strip(" .,'!?")
        # Avoid accidentally interpreting common request words as a person's name.
        if person and person not in {
            "me", "myself", "all", "everyone", "anyone", "the team",
            "the manager", "the owner", "the assignee", "current user",
        }:
            return person
    return None




def _extract_requested_project(message: str, messages: list[dict] | None = None) -> str | None:
    """Extract a project name, resolving references such as 'this project' from chat context."""
    text = _normal_text(message)
    generic_reference = bool(re.search(r"\b(?:this|that|same|current)\s+project\b", text))

    patterns = (
        r"\b(?:inside|within|under|in)\s+(?:the\s+)?(?:project\s+)?(.+?)(?=\s+(?:with|where|whose|assigned|owned|having|and\s+with)\b|[?.!,;]|$)",
        r"\b(?:tasks?|issues?|milestones?)\s+(?:inside|within|under|in|of)\s+(?:the\s+)?(?:project\s+)?(.+?)(?=[?.!,;]|$)",
        r"\b(?:status|progress|details?)\s+(?:of|for)\s+(?:the\s+)?(?:project\s+)?(.+?)(?=[?.!,;]|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        name = re.sub(r"\s+", " ", match.group(1)).strip(" .,'!?")
        if name and name not in {"the project", "a project", "any project", "project", "this project", "that project", "same project", "current project"}:
            return name

    if generic_reference and messages:
        # Prefer a project explicitly named in the user's earlier turns. An
        # assistant response may list several projects and should not override
        # the project the user was discussing.
        for prior in reversed(messages):
            if prior.get("role") != "user":
                continue
            prior_text = str(prior.get("content") or "")
            if re.search(r"\b(?:inside|within|under|in|of)\b", prior_text, re.I):
                prior_name = _extract_requested_project(prior_text, None)
                if prior_name and prior_name not in {
                    "the project", "a project", "any project", "project",
                    "this project", "that project", "same project", "current project",
                }:
                    return prior_name

        # Fallback: resolve from a specific assistant statement, but avoid
        # interpreting a list of available projects as the current project.
        for prior in reversed(messages):
            if prior.get("role") != "assistant":
                continue
            content = str(prior.get("content") or "")
            content = re.sub(r"<[^>]+>", " ", html.unescape(content))
            candidates = re.findall(
                r"(?:Project:\s*|inside\s+|within\s+|in\s+)([A-Za-z][A-Za-z0-9 _.-]*?)(?=\s*\(|\s*[—–,;.!?]|$)",
                content,
                flags=re.IGNORECASE,
            )
            for candidate in reversed(candidates):
                candidate = re.sub(r"\s+", " ", candidate).strip(" .,'!?-—–")
                if candidate and candidate.casefold() not in {"the project", "this project", "that project"}:
                    return candidate
    return None


def _canonical_project_text(value) -> str:
    """Normalize project names, including numeric suffixes such as 11 vs 011."""
    text = _normal_text(value)
    # Treat numeric tokens with leading zeroes as equivalent: "test 011" == "test 11".
    text = re.sub(r"\d+", lambda match: str(int(match.group(0))), text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _record_project_names(record) -> list[str]:
    """Collect project names from common flat and nested task API shapes."""
    if not isinstance(record, dict):
        return []
    candidates = []
    for key in (
        "project_name", "projectName", "project", "project_title",
        "projectTitle", "project_display_name", "projectDisplayName",
    ):
        value = record.get(key)
        if isinstance(value, dict):
            for name_key in ("name", "project_name", "projectName", "title", "project_title"):
                if value.get(name_key) not in (None, ""):
                    candidates.append(str(value[name_key]))
        elif isinstance(value, str):
            candidates.append(value)
    for key, value in _walk_values(record):
        normalized_key = _normalize_field_name(key)
        if normalized_key in {"project_name", "project_title", "project_display_name"} and isinstance(value, str):
            candidates.append(value)
    # De-duplicate while preserving the original order.
    return list(dict.fromkeys(candidate.strip() for candidate in candidates if candidate.strip()))


def _record_project_matches(record, requested_project: str) -> bool:
    """Match by normalized project name; numeric suffix leading zeroes are ignored."""
    wanted = _canonical_project_text(requested_project)
    if not wanted:
        return False
    for candidate in _record_project_names(record):
        actual = _canonical_project_text(candidate)
        if actual and actual == wanted:
            return True
    return False


def _filter_records_by_project(payload, requested_project: str):
    """Filter records by project; ask conversationally if no exact normalized match exists."""
    records, parent, key = _extract_record_list(payload)
    if records is None:
        return payload, None

    available_names = list(dict.fromkeys(
        name for record in records for name in _record_project_names(record)
    ))
    filtered = [record for record in records if _record_project_matches(record, requested_project)]

    if not filtered:
        # Do not silently substitute a merely similar project. Preserve authorized
        # project names so the final renderer can ask the user to clarify.
        clarification = {
            "clarification": (
                f"I couldn't find tasks matching the project name '{requested_project}'. "
                "Which project did you mean?"
            ),
            "requested_project": requested_project,
            "available_projects": available_names[:8],
            "filtered_count": 0,
            "total_count": 0,
            "has_more": False,
        }
        return clarification, 0

    if parent is None:
        return filtered, len(filtered)
    parent[key] = filtered
    if isinstance(payload, dict):
        payload["filtered_count"] = len(filtered)
        payload["total_count"] = len(filtered)
        payload["has_more"] = len(filtered) > 5
    for target in (parent, payload):
        if not isinstance(target, dict):
            continue
        for count_key in (
            "total", "total_count", "totalCount", "filtered_count", "filteredCount",
            "count", "total_records", "totalRecords", "total_items", "totalItems",
            "total_tasks", "totalTasks",
        ):
            if count_key in target and isinstance(target[count_key], (int, float)) and not isinstance(target[count_key], bool):
                target[count_key] = len(filtered)
        for more_key in ("has_more", "hasMore", "has_next", "hasNext"):
            if more_key in target:
                target[more_key] = len(filtered) > 5
    return payload, len(filtered)


def _update_filtered_count_metadata(payload, filtered_count: int):
    """Keep pagination metadata aligned with the post-tool-filtered result set."""
    records, parent, _ = _extract_record_list(payload)
    targets = []
    if isinstance(payload, dict):
        targets.append(payload)
    if isinstance(parent, dict) and parent is not payload:
        targets.append(parent)
    count_keys = (
        "total", "total_count", "totalCount", "filtered_count", "filteredCount",
        "count", "total_projects", "totalProjects", "projects_count", "projectsCount",
        "total_tasks", "totalTasks", "total_records", "totalRecords",
        "total_items", "totalItems",
    )
    for target in targets:
        for key in count_keys:
            if key in target and isinstance(target[key], (int, float)) and not isinstance(target[key], bool):
                target[key] = filtered_count
        if "has_more" in target:
            target["has_more"] = filtered_count > 5
        if "hasMore" in target:
            target["hasMore"] = filtered_count > 5
        if "has_next" in target:
            target["has_next"] = filtered_count > 5
        if "hasNext" in target:
            target["hasNext"] = filtered_count > 5
    if isinstance(payload, dict):
        payload["filtered_count"] = filtered_count
        payload["total_count"] = filtered_count
        payload["has_more"] = filtered_count > 5


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
    # DETERMINISTIC LIST RESPONSE (NO NVIDIA ROUND-TRIP)
    # --------------------------------------------------------

    # Simple list requests can be rendered directly from authorized tool data.
    # This guarantees the five-record + View More contract and avoids failing
    # the whole list flow when NVIDIA rejects an otherwise successful tool call.
    if has_tool_result and _is_simple_list_request(user_message):
        fallback = _safe_html_fallback(messages, user_message)
        if fallback:
            return {
                "messages": messages,
                "final_response": fallback,
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

            latest_user_message = next((
                str(message.get("content") or "")
                for message in reversed(messages)
                if message.get("role") == "user"
            ), "")

            # Scope is determined from the user's wording for both projects and tasks:
            #   "my ..." / "assigned to me" -> scope=mine
            #   generic list or "all ..." -> scope=all
            #   named-person request -> scope=all, then filter returned records locally
            #     by the requested relationship (assignee by default; owner if explicit).
            # The API still enforces the authenticated user's PMS authorization.
            current_user_query = _wants_assigned_to_current_user(latest_user_message)
            requested_person = _extract_requested_person(latest_user_message)
            requested_project = _extract_requested_project(latest_user_message, messages)
            unresolved_project_reference = bool(re.search(
                r"\b(?:this|that|same|current)\s+project\b", _normal_text(latest_user_message)
            )) and not requested_project
            for post_tool_filter in ("person_name", "assignee_name", "owner_name"):
                arguments.pop(post_tool_filter, None)
            arguments["scope"] = "mine" if current_user_query else "all"

            try:
                result = await tool_function(
                    state["access_token"],
                    arguments,
                )
            except Exception as exc:
                # A timeout should not crash the entire chat endpoint with HTTP 500.
                # Keep it distinct from an empty successful result so the renderer
                # can tell the user retrieval failed and invite a retry.
                exception_names = {
                    cls.__name__.casefold()
                    for cls in type(exc).__mro__
                }
                if any("timeout" in name for name in exception_names):
                    logger.warning(
                        "Read tool timed out (tool=%s, exception=%s)",
                        tool_name,
                        type(exc).__name__,
                    )
                    result = {
                        "error": "The PMS request timed out before the records could be retrieved.",
                        "error_code": "read_timeout",
                        "tool": tool_name,
                    }
                else:
                    logger.exception(
                        "Read tool execution failed (tool=%s)",
                        tool_name,
                    )
                    raise

            # Apply user/person filtering only AFTER the read tool has returned.
            # If the user asked for all records, leave the authorized result intact.
            entity_meta = ENTITY_META.get(tool_name)

            # For project-specific questions, filter the projects collection too.
            # Project records commonly use `name`/`title`, while task records use
            # `project_name`; support both shapes and normalize leading zeroes.
            if entity_meta and entity_meta[0] == "project" and requested_project:
                project_records, project_parent, project_key = _extract_record_list(result)
                if project_records is not None:
                    wanted = _canonical_project_text(requested_project)
                    def _project_record_matches(record):
                        names = []
                        if isinstance(record, dict):
                            for field in ("name", "title", "project_name", "projectName", "project_title", "projectTitle"):
                                value = record.get(field)
                                if isinstance(value, str) and value.strip():
                                    names.append(value)
                        return any(
                            _canonical_project_text(name) == wanted
                            for name in names
                            if _canonical_project_text(name)
                        )
                    matching_projects = [record for record in project_records if _project_record_matches(record)]
                    if matching_projects:
                        if project_parent is None:
                            result = matching_projects
                        else:
                            project_parent[project_key] = matching_projects
                            if isinstance(result, dict):
                                result["filtered_count"] = len(matching_projects)
                                result["total_count"] = len(matching_projects)
                                result["has_more"] = False
                    else:
                        available = []
                        for record in project_records:
                            if isinstance(record, dict):
                                value = next((record.get(field) for field in ("name", "title", "project_name", "projectName") if record.get(field)), None)
                                if value:
                                    available.append(str(value))
                        result = {
                            "clarification": f"I couldn't find a project matching '{requested_project}'. Which project did you mean?",
                            "requested_project": requested_project,
                            "available_projects": available[:8],
                            "filtered_count": 0,
                            "total_count": 0,
                            "has_more": False,
                        }
            if entity_meta and current_user_query and tool_name == "get_my_tasks":
                # "My tasks" specifically means tasks assigned to the authenticated
                # user. The tool call uses scope=mine, and this post-tool check ensures
                # returned rows are actually associated through assignee fields rather
                # than merely owned/created by the current user.
                try:
                    current_user = state.get("current_user")
                    if current_user is None:
                        current_user = await _current_user_details(state["access_token"])
                    if not isinstance(current_user, (dict, list)):
                        current_user = {
                            "id": getattr(current_user, "id", None),
                            "user_id": getattr(current_user, "user_id", None),
                            "first_name": getattr(current_user, "first_name", None),
                            "last_name": getattr(current_user, "last_name", None),
                            "name": " ".join(filter(None, [
                                getattr(current_user, "first_name", None),
                                getattr(current_user, "last_name", None),
                            ])).strip(),
                            "username": getattr(current_user, "username", None),
                            "email": getattr(current_user, "email", None),
                        }
                    current_user_identities = _current_user_identity_values(current_user)
                    if not current_user_identities:
                        raise ValueError("Authenticated user has no usable identity fields")
                    result, filtered_count = _filter_records_for_current_user(
                        result, "task", current_user_identities, include_owner=False
                    )
                    _update_filtered_count_metadata(result, filtered_count)
                    logger.info(
                        "Applied current-user assignee filter after tool result "
                        "(tool=%s, matched=%s)", tool_name, filtered_count,
                    )
                except Exception:
                    logger.exception("Could not verify current-user task assignees")
                    result = {"error": "Could not verify tasks assigned to the authenticated user."}

            elif entity_meta and requested_person:
                entity_type = entity_meta[0]
                person_identities = {_normal_text(requested_person)}
                explicit_owner_query = bool(re.search(
                    r"\b(?:owned by|owner is|whose owner is)\b",
                    latest_user_message,
                    re.IGNORECASE,
                ))
                # Task "assigned to X" means assignee only. Explicit owner queries
                # use owner fields; project matching may use relevant project
                # relationship fields returned by PMS.
                include_owner = entity_type != "task" or explicit_owner_query
                result, filtered_count = _filter_records_for_current_user(
                    result, entity_type, person_identities, include_owner=include_owner
                )
                _update_filtered_count_metadata(result, filtered_count)
                logger.info(
                    "Applied post-tool named-person filter (tool=%s, person=%s, matched=%s)",
                    tool_name, requested_person, filtered_count,
                )

            # A deictic reference such as 'this project' must resolve from chat history.
            # If history doesn't identify a project, ask the user rather than showing
            # unfiltered tasks or claiming that no tasks exist.
            if entity_meta and unresolved_project_reference and entity_meta[0] in {"task", "issue", "milestone", "timelog"}:
                available_records, _, _ = _extract_record_list(result)
                available_projects = list(dict.fromkeys(
                    name for record in (available_records or []) for name in _record_project_names(record)
                ))
                result = {
                    "clarification": "Which project do you mean by 'this project'? Please tell me the project name.",
                    "available_projects": available_projects[:8],
                }

            # Project-context filtering applies to task/issue/milestone records.
            # It is performed after the authorized read tool returns and before
            # the final-node five-record presentation limit.
            elif entity_meta and requested_project and entity_meta[0] in {"task", "issue", "milestone", "timelog"}:
                result, project_filtered_count = _filter_records_by_project(
                    result, requested_project
                )
                if project_filtered_count is not None:
                    logger.info(
                        "Applied project filter (tool=%s, project=%s, matched=%s)",
                        tool_name, requested_project, project_filtered_count,
                    )

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

    messages = state.get("messages", []) or []
    current_user_message = next(
        (
            str(message.get("content") or "")
            for message in reversed(messages)
            if message.get("role") == "user"
        ),
        "",
    )
    current_user_index = max(
        (i for i, message in enumerate(messages) if message.get("role") == "user"),
        default=-1,
    )
    current_turn = messages[current_user_index + 1:] if current_user_index >= 0 else messages
    has_read_result = any(
        message.get("role") == "tool"
        and (
            message.get("name") in READ_TOOLS
            or message.get("tool_name") in READ_TOOLS
            or message.get("tool_call_id")
        )
        for message in current_turn
    )

    # List, filter, and sort results are rendered deterministically from the
    # authorized tool payload in final_node. Skip a second LLM round-trip so a
    # large result set (e.g. thousands of tasks) is not sent back into context.
    if has_read_result and _is_simple_list_request(current_user_message):
        return "final"

    # Keep the existing LLM analysis/tool loop for non-list analytical questions.
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



# ============================================================
# REQUESTED SORTING (APPLIED TO FRESH AUTHORIZED TOOL RESULTS)
# ============================================================

_SORT_FIELD_ALIASES = {
    "priority": ("priority", "priority_name", "priorityName", "priority_label", "priorityLabel", "priority_value", "priorityValue", "priority_id", "priorityId"),
    "status": ("status", "status_name", "statusName", "task_status", "taskStatus", "project_status", "projectStatus", "state", "state_name", "stateName"),
    "due date": ("due_date", "dueDate", "deadline", "expected_end_date", "expectedEndDate", "end_date", "endDate", "target_date", "targetDate"),
    "start date": ("start_date", "startDate", "planned_start_date", "plannedStartDate"),
    "created date": ("created_at", "createdAt", "created_date", "createdDate"),
    "updated date": ("updated_at", "updatedAt", "modified_at", "modifiedAt", "updated_date", "updatedDate"),
    "completion": ("completion", "completion_percentage", "completionPercentage", "progress", "progress_percentage", "progressPercentage", "percent_complete", "percentComplete"),
    "name": ("name", "title", "task_name", "taskName", "project_name", "projectName", "issue_name", "issueName", "milestone_name", "milestoneName"),
    "budget": ("budget", "project_budget", "projectBudget", "estimated_cost", "estimatedCost", "cost"),
    "owner": ("owner", "owner_name", "ownerName", "task_owner", "task_owner_name", "taskOwner", "taskOwnerName"),
    "assignee": ("assignee", "assignees", "assignee_name", "assigneeName", "assigned_to", "assignedTo", "assigned_users", "assignedUsers"),
}


def _detect_sort_request(user_message: str):
    """Detect an actual ordering request, not a request to filter by a value.

    Examples:
      - "sort tasks by priority high to low" -> priority descending by rank
      - "order projects by name A to Z" -> name ascending
      - "show tasks which have priority medium" -> not sorting; filtering handles it
      - "sort tasks which are having priority as medium" -> filtering, despite the
        colloquial use of the word "sort".
    """
    text = _normal_text(user_message)
    if not text:
        return None

    # A field/value condition is filtering unless the user separately specifies
    # an ordering field/direction (e.g. "medium-priority tasks, sorted by name").
    value_filter_language = bool(re.search(
        r"\b(?:which\s+(?:are\s+)?(?:having|have|has)|with|whose|having)\b.*"
        r"\b(?:priority|status|state)\s*(?:is|as|=|equals?|equal\s+to)?\s*"
        r"(?:critical|urgent|highest|high|medium|normal|low|lowest|open|active|"
        r"in[\s_-]*progress|completed|closed|blocked|pending|overdue)\b",
        text,
    ))
    explicit_ordering = bool(re.search(
        r"\b(?:sort(?:ed|ing)?\s+by|order(?:ed)?\s+by|rank(?:ed|ing)?\s+by)\b",
        text,
    )) or bool(re.search(
        r"\b(?:ascending|descending|\basc\b|\bdesc\b|high\s+to\s+low|"
        r"low\s+to\s+high|highest\s+to\s+lowest|lowest\s+to\s+highest|"
        r"a\s+to\s+z|z\s+to\s+a|earliest\s+first|latest\s+first|"
        r"oldest\s+first|newest\s+first|smallest\s+to\s+largest|"
        r"largest\s+to\s+smallest)\b",
        text,
    ))
    if value_filter_language and not explicit_ordering:
        return None

    has_sort_trigger = bool(re.search(
        r"\b(?:sort|sorts|sorted|sorting|order|ordered|rank|ranking)\b",
        text,
    )) or explicit_ordering
    if not has_sort_trigger:
        return None

    field_patterns = (
        ("due date", r"\b(?:due\s*date|deadline|due)\b"),
        ("start date", r"\bstart\s*date\b"),
        ("created date", r"\bcreated\s*date|creation\s*date\b"),
        ("updated date", r"\bupdated\s*date|modified\s*date\b"),
        ("priority", r"\bpriorit(?:y|ies)\b"),
        ("status", r"\bstatus(?:es)?\b"),
        ("completion", r"\b(?:completion|progress|percent(?:age)?\s*complete)\b"),
        ("budget", r"\b(?:budget|cost)\b"),
        ("owner", r"\bowner\b"),
        ("assignee", r"\b(?:assignee|assigned\s+to)\b"),
        ("name", r"\b(?:name|alphabetically|title)\b"),
    )
    field = next((name for name, pattern in field_patterns if re.search(pattern, text)), None)
    if not field:
        return None

    ascending_words = bool(re.search(
        r"\b(?:ascending|asc|lowest\s+to\s+highest|low\s+to\s+high|"
        r"earliest\s+first|oldest\s+first|a\s+to\s+z|smallest\s+to\s+largest)\b",
        text,
    ))
    descending_words = bool(re.search(
        r"\b(?:descending|desc|highest\s+to\s+lowest|high\s+to\s+low|"
        r"latest\s+first|newest\s+first|z\s+to\s+a|largest\s+to\s+smallest)\b",
        text,
    ))
    if field == "priority":
        # Priority rank order is High -> Medium -> Low by default.
        reverse = ascending_words
    elif field == "status":
        reverse = descending_words
    elif ascending_words:
        reverse = False
    elif descending_words:
        reverse = True
    else:
        reverse = field in {"updated date", "created date"}
    return field, reverse


def _sort_value(record, field):
    """Extract a sort value from direct or nested API fields, never parent-project values for task fields."""
    aliases = _SORT_FIELD_ALIASES.get(field, ())
    value = None

    # Reuse the field extractor for common fields; it handles snake/camel case,
    # nested display objects, and avoids reading a parent project's status/priority
    # as though it were the task's own value.
    if field in _FILTER_FIELD_ALIASES:
        extracted = _field_values_for_record(record, field)
        if extracted:
            value = extracted[0]

    # Other supported sort fields (e.g. name/budget) may not be filter fields.
    if value in (None, "") and isinstance(record, dict):
        normalized_aliases = {_normalize_field_name(alias) for alias in aliases}
        def walk(obj, inside_project=False):
            if isinstance(obj, dict):
                for key, child in obj.items():
                    norm = _normalize_field_name(key)
                    is_project = inside_project or norm in {"project", "project_details", "project_record", "parent_project"}
                    if norm in normalized_aliases and child not in (None, "") and not (field in {"priority", "status"} and is_project):
                        return child
                    if isinstance(child, (dict, list)):
                        found = walk(child, is_project)
                        if found not in (None, ""):
                            return found
            elif isinstance(obj, list):
                for child in obj:
                    found = walk(child, inside_project)
                    if found not in (None, ""):
                        return found
            return None
        value = walk(record)

    value = _display_field_value(value) if value not in (None, "") else None
    if value in (None, ""):
        return (1, "")  # Missing values always sort last.

    text = _normal_text(value)
    compact = re.sub(r"[^a-z0-9]+", "", text)
    if field == "priority":
        ranks = {"urgent": 0, "critical": 0, "highest": 0, "high": 1, "medium": 2, "normal": 2, "low": 3, "lowest": 4}
        return (0, ranks.get(compact, 99), compact)
    if field == "status":
        ranks = {"overdue": 0, "open": 1, "new": 1, "todo": 1, "inprogress": 2, "active": 2, "inreview": 3, "review": 3, "blocked": 4, "onhold": 5, "completed": 6, "complete": 6, "closed": 7, "done": 7, "cancelled": 8, "canceled": 8}
        return (0, ranks.get(compact, 99), compact)
    if field in {"due date", "start date", "created date", "updated date"}:
        return (0, text[:10], text[11:])
    if field in {"completion", "budget"}:
        match = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
        return (0, float(match.group()) if match else float("inf"))
    return (0, text)


def _apply_requested_sort(messages, user_message: str):
    """Sort every current-turn authorized tool payload by the requested field.

    Sorting happens before the response's five-record presentation limit. This
    only reorders records already returned by authorized PMS read tools.
    """
    sort_request = _detect_sort_request(user_message)
    if not sort_request:
        return messages, False
    field, reverse = sort_request
    sorted_messages = [dict(message) for message in messages]

    for message in sorted_messages:
        if message.get("role") != "tool":
            continue
        raw = message.get("content") or ""
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        records, parent, key = _extract_record_list(payload)
        if records is None or not records:
            continue
        # Stable sort: records with no requested value are placed last even for
        # descending sorts, rather than being promoted to the top.
        present = [record for record in records if _sort_value(record, field)[0] == 0]
        missing = [record for record in records if _sort_value(record, field)[0] != 0]
        present.sort(key=lambda record: _sort_value(record, field), reverse=reverse)
        sorted_records = present + missing
        if parent is None:
            payload = sorted_records
        else:
            parent[key] = sorted_records
        message["content"] = json.dumps(payload, ensure_ascii=False, default=str)

    logger.info("Applied requested PMS sort (field=%s, reverse=%s)", field, reverse)
    return sorted_messages, True


def _is_simple_list_request(user_message: str) -> bool:
    """Identify straightforward entity-list requests that should use deterministic HTML."""
    text = (user_message or "").strip().lower()
    # Sorting requests for entity lists MUST use deterministic rendering too,
    # so the renderer preserves the graph's order and prints the requested field.
    if _detect_sort_request(text):
        return bool(re.search(
            r"\b(?:tasks?|projects?|issues?|defects?|milestones?|task\s*lists?|tasklists?|time\s*logs?|timesheets?)\b",
            text,
        ))
    # Explicit field=value filters are deterministic list operations. They must
    # be filtered in application code before the five-record limit is applied.
    if _detect_attribute_filter(text) and re.search(
        r"\b(?:tasks?|projects?|issues?|defects?|milestones?|task\s*lists?|tasklists?|time\s*logs?|timesheets?)\b",
        text,
    ):
        return True
    # "My projects" / "what are my projects" should use the same deterministic
    # five-record + View More formatter even when the wording has no list verb.
    asks_to_list = bool(re.search(
        r"\b(?:show|list|get|display|give me|what are|what is|find|fetch)\b", text
    )) or bool(re.search(
        r"\bmy\s+(?:assigned\s+)?(?:tasks?|projects?|issues?|defects?|milestones?)\b",
        text,
    ))
    asks_for_entity = bool(re.search(
        r"\b(?:tasks?|projects?|issues?|defects?|milestones?|task\s*lists?|tasklists?|time\s*logs?|timesheets?)\b", text
    ))
    # Status/progress questions are entity-specific questions, not list requests.
    # They must reach the deterministic status handler instead of being rendered
    # as a project collection.
    # Filtering qualifiers remain list requests unless the user asks for the
    # status/progress itself.
    detail_or_analysis = bool(re.search(
        r"\b(?:why|how|compare|summari[sz]e|percentage|progress|status|completion|complete|completed|overall|details? of|explain|overdue|due date)\b", text
    ))
    return asks_to_list and asks_for_entity and not detail_or_analysis


def _deterministic_project_status_response(messages, user_message):
    """Answer a project-status follow-up from the filtered authorized project data."""
    text = _normal_text(user_message)
    if not re.search(r"\bstatus\b", text):
        return None
    requested_project_for_status = _extract_requested_project(user_message, messages)
    if not re.search(r"\bproject\b", text) and not requested_project_for_status:
        return None

    current_user_index = max(
        (i for i, message in enumerate(messages) if message.get("role") == "user"),
        default=-1,
    )
    turn = messages[current_user_index + 1:] if current_user_index >= 0 else messages
    tool_names = {}
    for message in turn:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls", []) or []:
                function = call.get("function", {}) or {}
                tool_names[call.get("id")] = function.get("name")

    for message in turn:
        if message.get("role") != "tool" or tool_names.get(message.get("tool_call_id")) != "get_my_projects":
            continue
        try:
            payload = json.loads(message.get("content") or "null")
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("clarification"):
            question = html.escape(str(payload["clarification"]))
            choices = payload.get("available_projects") or []
            extra = ""
            if choices:
                extra = "<p>Available projects:</p><ul>" + "".join(
                    f"<li>{html.escape(str(choice))}</li>" for choice in choices
                ) + "</ul>"
            return {"response_type": "chat", "response": f"<p>{question}</p>{extra}", "data": []}
        records, _, _ = _extract_record_list(payload)
        if not records:
            continue
        # Prefer the project resolved for this turn. Never silently report the
        # first project if multiple candidate projects were returned.
        valid_records = [record for record in records if isinstance(record, dict)]
        if not valid_records:
            continue

        requested_project = _extract_requested_project(user_message, messages)
        selected_record = None
        if requested_project:
            requested_key = _canonical_project_text(requested_project)
            for candidate in valid_records:
                if any(
                    _canonical_project_text(candidate_name) == requested_key
                    for candidate_name in _record_project_names(candidate)
                ):
                    selected_record = candidate
                    break
        if selected_record is None and len(valid_records) == 1:
            selected_record = valid_records[0]
        if selected_record is None:
            choices = []
            for candidate in valid_records[:5]:
                candidate_name = next((candidate.get(k) for k in (
                    "name", "project_name", "projectName", "title", "project_title"
                ) if candidate.get(k)), None)
                if candidate_name:
                    choices.append(str(candidate_name))
            if choices:
                safe_choices = "".join(f"<li>{html.escape(choice)}</li>" for choice in choices)
                return {
                    "response_type": "chat",
                    "response": "<p>Which project would you like the status for?</p><ul>" + safe_choices + "</ul>",
                    "data": [],
                }
            continue

        record = selected_record
        name = next((record.get(k) for k in ("name", "project_name", "projectName", "title", "project_title") if record.get(k)), "the requested project")

        # Status may be a string or a related master-data object, e.g.
        # {"id": 43, "value": "Active", "label": "Active"}.
        status = None
        for status_key in ("status", "project_status", "projectStatus", "status_name", "statusName", "state"):
            value = record.get(status_key)
            if value in (None, ""):
                continue
            if isinstance(value, dict):
                value = next((value.get(k) for k in (
                    "value", "label", "name", "status_name", "statusName"
                ) if value.get(k) not in (None, "")), None)
            if isinstance(value, (str, int, float)) and str(value).strip():
                status = value
                break

        safe_name = html.escape(str(name))
        if status is None:
            return {
                "response_type": "chat",
                "response": f"<p>I found <strong>{safe_name}</strong>, but its status field wasn't included in the project data returned to me.</p>",
                "data": [],
            }
        return {
            "response_type": "chat",
            "response": f"<p>The status of <strong>{safe_name}</strong> is <strong>{html.escape(str(status))}</strong>.</p>",
            "data": [],
        }
    return None


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

    # Apply explicit field=value filters to fresh authorized tool results before
    # sorting, final LLM synthesis, and the five-record display limit.
    attribute_filter = _detect_attribute_filter(user_message)
    filter_applied = False
    if current_turn_messages and attribute_filter:
        messages, attribute_filter, filter_applied = _apply_attribute_filter_to_messages(
            messages, user_message
        )
        # Refresh the current-turn slice because tool message payloads were replaced.
        user_message_index = max(
            (i for i, message in enumerate(messages) if message.get("role") == "user"),
            default=-1,
        )
        current_turn_messages = messages[user_message_index + 1:] if user_message_index >= 0 else messages

    # Apply explicit sorting to fresh authorized tool results before any
    # final rendering or five-record display limit.
    sort_applied = False
    if current_turn_messages:
        sorted_turn, sort_applied = _apply_requested_sort(current_turn_messages, user_message)
        if sort_applied:
            messages = messages[:user_message_index + 1] + sorted_turn
            current_turn_messages = sorted_turn

    # A list filter such as "tasks with status In Progress" must not be treated
    # as a request for one project's status or passed into project-name matching.
    if (
        not attribute_filter
        and any(message.get("role") == "tool" for message in current_turn_messages)
    ):
        status_response = _deterministic_project_status_response(messages, user_message)
        if status_response:
            return {"final_response": status_response}

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

    if sort_applied:
        sort_request = _detect_sort_request(user_message)
        if sort_request:
            sort_field, sort_reverse = sort_request
            direction_label = "descending" if sort_reverse else "ascending"
            final_messages.append({
                "role": "system",
                "content": (
                    f"SORT REQUEST DISPLAY REQUIREMENT: The graph has already sorted the fresh authorized records by {sort_field} ({direction_label}). "
                    f"Display the actual {sort_field} value next to EACH displayed record, in addition to its normal identifying fields. "
                    "Do not omit the sort field value. Keep the graph-provided order exactly, and do not re-sort. "
                    "If the value is absent in a record, label it 'value unavailable' rather than guessing."
                ),
            })

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
                    "1. TOOL DATA is the source of truth. Never invent, change, merge, duplicate, or remove records unless the user explicitly requests an operation. If the user requests sorting, use the graph-sorted fresh tool records in their given order; do not re-sort or guess values.\n"
                    "2. NEVER dump all available fields. For normal list/show/get requests use only the compact default fields:\n"
                    "PROJECT = name + public_id (or id) ONLY.\n"
                    "TASK = name + public_id (or id) + project ONLY.\n"
                    "ISSUE = name + public_id (or id) + project ONLY.\n"
                    "MILESTONE = name + public_id (or id) + project ONLY.\n"
                    "TASKLIST = name + public_id (or id) + project ONLY.\n"
                    "3. Status, priority, completion, dates, owner, assignee, manager, delivery head, team, description, hours, billing, timestamps, and all other fields are FORBIDDEN by default. Add a field ONLY when the user explicitly asks for it. IMPORTANT SORTING EXCEPTION: when the user asks to sort/order/rank by a field (for example priority, status, due date, start date, completion, budget, owner, assignee, or name), show that requested sort field's actual value beside EVERY displayed record, even if they did not separately say 'show the field'. Example: <li><a href=\"...\">Task name</a> (ID) — Priority: High</li>. For due-date sorting, show Due date: YYYY-MM-DD; for status sorting, show Status: In Progress. Use the exact value present in the tool record, support field-name aliases/nested display values, and never invent a missing value. If a record has no value for the sort field, show that the field is unavailable for that record.\n"
                    "4. For list requests, show at most the 5 most recent records (prefer updated_at/created_at/date descending; if no date exists, preserve TOOL DATA order). Use exactly one <ol> with one <li> per displayed record. If more than 5 records are available, append a View more link to the entity collection route (projects=/projects, tasks=/tasks, issues=/issues, milestones=/milestones, tasklists=/tasklists, time logs=/time-log). Do not state a total count or say 'showing 5 of N', because the returned count may not represent all records.\n"
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

    logger.debug(
        "Prepared final-node input (query_length=%s, current_turn_tool_count=%s, tool_data_count=%s)",
        len(user_message),
        sum(1 for m in current_turn_messages if m.get("role") == "tool"),
        len(tool_data) if isinstance(tool_data, list) else int(isinstance(tool_data, dict)),
    )

    # Deterministic list presentation: do not make the final list rendering
    # depend on NVIDIA. This guarantees the 5-record limit and View More link
    # even when the final NVIDIA request is rejected (for example HTTP 400).
    # The read tools have already run and the fallback uses their current-turn
    # authorized results and pagination/count metadata.
    if _is_simple_list_request(user_message):
        deterministic_list = _safe_html_fallback(messages, user_message)
        if deterministic_list:
            logger.info(
                "Returning deterministic five-record list response (user_query=%r)",
                user_message[:160],
            )
            return {"final_response": deterministic_list}

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

        # Enforce the 5-record + View More contract even when NVIDIA returns a
        # valid response containing too many records. The fallback uses the
        # actual current-turn tool payload and authoritative count metadata.
        if _is_simple_list_request(user_message):
            deterministic_list = _safe_html_fallback(messages, user_message)
            if deterministic_list:
                normalized_result = deterministic_list

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

        # Normalize malformed LLM HTML such as <a><strong><a>Task</a></strong></a>.
        # Keep the innermost link and unwrap any ancestor anchor that contains it.
        for inner_anchor in list(soup.find_all("a")):
            ancestor_anchor = inner_anchor.find_parent("a")
            if ancestor_anchor is not None:
                ancestor_anchor.unwrap()

        # Prefer longer names first, so a shorter name contained in a longer
        # project/task name does not steal the match.
        entities.sort(key=lambda item: len(item[0]), reverse=True)

        def replace_in_text_node(text_node):
            original = str(text_node)
            if not original.strip():
                return
            # Text may be nested inside <strong>/<span> inside an existing link.
            # Check all ancestors, not only the immediate parent, otherwise this
            # pass can generate invalid nested <a> elements.
            if text_node.find_parent("a"):
                return

            matches = []
            for name, url in entities:
                # Escape literal entity names and require non-alphanumeric boundaries.
                # This prevents a shorter name such as "test 1" from linking inside
                # "test 12" or another longer identifier.
                pattern = re.compile(
                    r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])",
                    re.IGNORECASE,
                )
                for found in pattern.finditer(original):
                    matches.append((found.start(), found.end(), url))

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