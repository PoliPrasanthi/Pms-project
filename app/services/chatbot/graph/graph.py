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

async def has_create_permission(
    access_token: str,
    entity_type: str,
) -> bool:

    permission_tool = TOOL_FUNCTIONS.get(
        "get_my_permissions"
    )

    if not permission_tool:
        return False

    permission_response = await permission_tool(
        access_token,
        {},
    )

    if not isinstance(
        permission_response,
        dict,
    ):
        return False

    permissions = permission_response.get(
        "permissions",
        {},
    )

    if not isinstance(
        permissions,
        dict,
    ):
        return False

    permission_key = (
        f"{entity_type}-create"
    )

    permission_value = permissions.get(
        permission_key
    )

    if permission_value is True:
        return True

    if isinstance(
        permission_value,
        str,
    ):
        return permission_value.strip().lower() in {
            "o",
            "a",
            "all",
            "true",
        }

    return False

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


def detect_creation_entity(
    message: str,
) -> str | None:
    text = (
        message
        .lower()
        .strip()
    )

    if "tasklist" in text:
        return "tasklist"

    if "task list" in text:
        return "tasklist"

    if "project" in text:
        return "project"

    if "task" in text:
        return "task"

    if "issue" in text:
        return "issue"

    if "milestone" in text:
        return "milestone"

    return None


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
                message.get("content", "")
                or ""
            )
            break


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


    permission_context_exists = any(
        message.get("role") == "system"
        and (
            "SESSION PERMISSIONS"
            in message.get("content", "")
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


    if is_permission_question(user_message):
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

            allowed = await has_create_permission(
                access_token=state["access_token"],
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
                synthetic_tool_call,
            ],
        }

        return {
            "messages": messages + [
                synthetic_message
            ],
        }


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
        "messages": messages + [message],
    }


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


            allowed = await has_create_permission(
                access_token=state["access_token"],
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

            print("\n" + "=" * 70)
            print("[GRAPH] TOOL OUTPUT")
            print("=" * 70)
            print(f"[GRAPH] Tool: {tool_name}")
            print(json.dumps(result, indent=2, default=str))
            print("=" * 70)

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
        "messages": messages + tool_messages,
    }


def route_after_tools(
    state: ChatState,
):
    if state.get("final_response"):
        return "final"

    return "llm"


def extract_json_object(
    content: str,
):
    if not content:
        return None

    cleaned = content.strip()

    if cleaned.startswith("```json"):
        cleaned = cleaned[len("```json"):]

        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]

    elif cleaned.startswith("```"):
        cleaned = cleaned[len("```"):]

        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]

    cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")

    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(
                cleaned[start:end + 1]
            )
        except json.JSONDecodeError:
            pass

    return None


def normalize_final_response(
    result,
):
    if not isinstance(
        result,
        dict,
    ):
        return None

    result.setdefault(
        "response_type",
        "chat",
    )

    result.setdefault(
        "response",
        "<p>I couldn't format the response.</p>",
    )

    result.setdefault(
        "data",
        [],
    )

    return result



def _html_value(value, level=0):
    if value is None:
        return ""

    if isinstance(value, bool):
        return "true" if value else "false"

    if isinstance(value, (str, int, float)):
        return html.escape(str(value))

    if level > 6:
        return html.escape(str(value))

    if isinstance(value, list):
        if not value:
            return "<p>No records returned.</p>"

        parts = ["<ul>"]
        for item in value[:100]:
            parts.append("<li>")
            parts.append(_html_value(item, level + 1))
            parts.append("</li>")
        if len(value) > 100:
            parts.append(
                f"<li>Showing first 100 of {len(value)} records.</li>"
            )
        parts.append("</ul>")
        return "".join(parts)

    if isinstance(value, dict):
        if not value:
            return "<p>No data returned.</p>"

        parts = ["<div>"]
        for key, item in value.items():
            label = html.escape(str(key).replace("_", " ").title())
            parts.append("<div style=\"margin-bottom:6px;\">")
            parts.append(f"<strong>{label}:</strong> ")
            if isinstance(item, (dict, list)):
                parts.append(_html_value(item, level + 1))
            else:
                parts.append(_html_value(item, level + 1))
            parts.append("</div>")
        parts.append("</div>")
        return "".join(parts)

    return html.escape(str(value))


def build_tool_results_html(messages):
    tool_name_by_id = {}

    for message in messages:
        if message.get("role") != "assistant":
            continue

        for tool_call in message.get("tool_calls", []) or []:
            tool_id = tool_call.get("id")
            function = tool_call.get("function", {}) or {}
            tool_name = function.get("name", "tool")
            if tool_id:
                tool_name_by_id[tool_id] = tool_name

    sections = []

    for message in messages:
        if message.get("role") != "tool":
            continue

        raw_content = message.get("content", "") or ""
        tool_name = tool_name_by_id.get(
            message.get("tool_call_id"),
            "PMS Tool",
        )

        try:
            parsed = json.loads(raw_content)
        except (TypeError, json.JSONDecodeError):
            parsed = raw_content

        sections.append(
            "<section style=\"margin-bottom:16px;\">"
            f"<h4>{html.escape(tool_name)}</h4>"
            f"{_html_value(parsed)}"
            "</section>"
        )

    if not sections:
        return "<p>No PMS tool data was returned.</p>"

    return "".join(sections)


def find_current_turn_messages(messages):
    user_index = -1

    for index in range(len(messages) - 1, -1, -1):
        if messages[index].get("role") == "user":
            user_index = index
            break

    if user_index == -1:
        return []

    return messages[user_index + 1:]


def extract_tool_data(messages):
    data = []

    for message in messages:
        if message.get("role") != "tool":
            continue

        raw_content = (
            message.get("content", "")
            or ""
        )

        try:
            parsed = json.loads(raw_content)
        except (TypeError, json.JSONDecodeError):
            parsed = raw_content

        data.append(parsed)

    return data


def tool_data_has_records(tool_data):
    if not isinstance(tool_data, list):
        return False

    def contains_non_empty_list(value):
        if isinstance(value, list):
            if value:
                return True
            return False

        if isinstance(value, dict):
            return any(
                contains_non_empty_list(item)
                for item in value.values()
            )

        return False

    return any(
        contains_non_empty_list(item)
        for item in tool_data
    )


def is_failed_format_response(result):
    if not isinstance(result, dict):
        return True

    response = str(
        result.get("response", "")
        or ""
    ).lower()

    failure_phrases = (
        "i couldn't format",
        "i could not format",
        "couldn't format the response",
        "could not format the response",
    )

    return any(
        phrase in response
        for phrase in failure_phrases
    )


def claims_no_records(result, tool_data):
    if not isinstance(result, dict):
        return False

    if not tool_data_has_records(tool_data):
        return False

    response = str(
        result.get("response", "")
        or ""
    ).lower()

    no_record_phrases = (
        "no projects were found",
        "no projects found",
        "no tasks were found",
        "no tasks found",
        "no issues were found",
        "no issues found",
        "no milestones were found",
        "no milestones found",
        "no task lists were found",
        "no task lists found",
        "no tasklists were found",
        "no tasklists found",
        "no records were found",
        "no records found",
    )

    return any(
        phrase in response
        for phrase in no_record_phrases
    )


async def format_tool_html_with_llm(
    user_message: str,
    tool_html: str,
    tool_data: list,
    permissions: dict,
):
    fallback_messages = [
        {
            "role": "system",
            "content": (
                "Return ONLY valid JSON with exactly these keys: "
                "response_type, response, data.\n\n"

                "Set response_type to 'chat'.\n\n"

                "The user asked a PMS data question.\n"
                "The application has provided the actual PMS "
                "tool result and a Python-generated HTML "
                "representation of that result.\n\n"

                "The TOOL DATA is the authoritative source of truth.\n"
                "The HTML is only a presentation representation.\n\n"

                "Rules:\n"
                "- Use only values present in TOOL DATA.\n"
                "- Never invent PMS information.\n"
                "- Never claim that records do not exist when "
                "TOOL DATA contains records.\n"
                "- Only say that no records were found when the "
                "relevant collection in TOOL DATA is actually empty.\n"
                "- Decide which fields to display based on the "
                "user's question.\n"
                "- You may omit irrelevant fields.\n"
                "- Do not add fields that are not present in TOOL DATA.\n"
                "- Keep the response concise and readable.\n"
                "- Use the Python-generated HTML structure when useful.\n"
                "- Return valid JSON only.\n\n"

                "TOOL DATA:\n"
                + json.dumps(
                    tool_data,
                    default=str,
                )
                + "\n\n"

                "PYTHON-GENERATED HTML:\n"
                + tool_html
            ),
        },
        {
            "role": "user",
            "content": user_message,
        },
    ]

    if permissions:
        fallback_messages.append(
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

    response = await chat_with_nvidia(
        messages=fallback_messages,
        json_mode=True,
    )

    choices = response.get(
        "choices",
        [],
    )

    if not choices:
        return None

    content = (
        choices[0]
        .get("message", {})
        .get("content", "")
        or ""
    ).strip()

    print("\n" + "=" * 70)
    print("[GRAPH] FALLBACK LLM RESULT")
    print("=" * 70)
    print(content)
    print("=" * 70)

    fallback_result = extract_json_object(content)

    if fallback_result is None:
        return None

    if is_failed_format_response(fallback_result):
        return None

    return normalize_final_response(
        fallback_result
    )


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
        if messages[index].get(
            "role"
        ) == "user":
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

    for message in current_turn_messages:
        if message.get("role") in {
            "assistant",
            "tool",
        }:
            final_messages.append(message)


    tool_data = extract_tool_data(
        current_turn_messages
    )

    tool_html = build_tool_results_html(
        current_turn_messages
    )

    print("\n" + "=" * 70)
    print("[GRAPH] TOOL DATA FOR FINAL RESPONSE")
    print("=" * 70)
    print(json.dumps(tool_data, indent=2, default=str))
    print("=" * 70)

    final_response = await chat_with_nvidia(
        messages=final_messages,
        json_mode=True,
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

        print("\n" + "=" * 70)
        print("[GRAPH] FINAL LLM RESULT")
        print("=" * 70)
        print(content)
        print("=" * 70)

        result = extract_json_object(content)

    if (
        result is not None
        and not is_failed_format_response(result)
        and not claims_no_records(result, tool_data)
    ):
        return {
            "final_response": normalize_final_response(result),
        }

    print("\n" + "=" * 70)
    print("[GRAPH] PYTHON-CONVERTED HTML")
    print("=" * 70)
    print(tool_html)
    print("=" * 70)

    fallback_result = await format_tool_html_with_llm(
        user_message=user_message,
        tool_html=tool_html,
        tool_data=tool_data,
        permissions=permissions,
    )

    if fallback_result is not None:
        print("\n" + "=" * 70)
        print("[GRAPH] FINAL FALLBACK RESPONSE")
        print("=" * 70)
        print(json.dumps(fallback_result, indent=2, default=str))
        print("=" * 70)

        return {
            "final_response": fallback_result,
        }

    return {
        "final_response": {
            "response_type": "chat",
            "response": tool_html,
            "data": tool_data,
        },
    }


def build_chat_graph():
    graph = StateGraph(ChatState)

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
