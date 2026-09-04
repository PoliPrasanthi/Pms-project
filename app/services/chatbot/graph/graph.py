import json

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


async def llm_node(
    state: ChatState,
):
    response = await chat_with_nvidia(
        messages=state["messages"],
        tools=PROJECT_TOOLS,
    )

    message = response.get(
        "choices",
        [{}],
    )[0].get(
        "message",
        {},
    )

    return {
        "messages": state["messages"] + [message]
    }


def route_after_llm(
    state: ChatState,
):
    last_message = state["messages"][-1]

    if last_message.get("tool_calls"):
        return "tools"

    return "final"

def detect_creation_entity(message: str) -> str | None:
    text = message.lower().strip()

    if "project" in text:
        return "project"

    if "task" in text:
        return "task"

    if "issue" in text:
        return "issue"

    if "milestone" in text:
        return "milestone"

    return None


async def tool_node(
    state: ChatState,
):
    messages = state["messages"]
    last_message = messages[-1]

    tool_calls = last_message.get(
        "tool_calls",
        [],
    )

    tool_messages = []
    creation_request = None

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

        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}

        if tool_name in CREATION_INTENT_TOOLS:

            entity_type = arguments.get(
                "entity_type",
            )

            creation_arguments = arguments.get(
                "arguments",
                {},
            )

            user_message = ""

            for message in reversed(messages):

                if message.get("role") == "user":
                    user_message = message.get(
                        "content",
                        "",
                    )
                    break

            detected_entity = detect_creation_entity(
                user_message
            )

            if detected_entity:
                entity_type = detected_entity

            permissions = state.get(
                "permissions",
                {},
            )

            permission_key = (
                f"{entity_type}-create"
            )

            permission_value = permissions.get(
                permission_key
            )

            allowed = (
                permission_value is True
                or permission_value in {
                    "O",
                    "A",
                    "All",
                }
            )

            creation_request = {
                "entity_type": entity_type,
                "arguments": creation_arguments,
                "permission_denied": not allowed,
            }

            continue

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

    result_state = {
        "messages": messages + tool_messages,
    }

    if creation_request:
        result_state["creation_request"] = (
            creation_request
        )

    return result_state

def route_after_tools(
    state: ChatState,
):
    creation_request = state.get(
        "creation_request"
    )

    if creation_request:

        if creation_request.get(
            "permission_denied"
        ):
            return "final"

        return "creation"

    return "llm"


async def final_node(
    state: ChatState,
):
    existing_response = state.get(
        "final_response",
    )

    if existing_response:
        return {
            "final_response": existing_response
        }

    creation_request = state.get(
        "creation_request"
    )

    if (
        creation_request
        and creation_request.get(
            "permission_denied"
        )
    ):
        entity_type = creation_request.get(
            "entity_type",
            "item",
        )

        return {
            "final_response": {
                "response_type": "chat",
                "response": (
                    f"<p>You don't have permission "
                    f"to create a {entity_type}.</p>"
                ),
                "data": {
                    "type": "permission_denied",
                    "entity_type": entity_type,
                },
            }
        }

    user_message = ""

    for message in state["messages"]:
        if message.get("role") == "user":
            user_message = message.get(
                "content",
                "",
            )

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

    for message in state["messages"]:
        if message.get("role") in {
            "assistant",
            "tool",
        }:
            final_messages.append(message)

    final_response = await chat_with_nvidia(
        messages=final_messages,
        json_mode=True,
    )

    final_message = final_response.get(
        "choices",
        [{}],
    )[0].get(
        "message",
        {},
    )

    content = (
        final_message.get(
            "content",
            "",
        )
        or ""
    )

    try:
        result = json.loads(content)

        if not isinstance(result, dict):
            result = {
                "response_type": "chat",
                "response": content,
                "data": {},
            }

    except json.JSONDecodeError:

        result = {
            "response_type": "chat",
            "response": content,
            "data": {},
        }

    return {
        "final_response": result
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
            "creation": END,
            "final": "final",
        },
    )

    graph.add_edge(
        "final",
        END,
    )

    return graph.compile()


chat_graph = build_chat_graph()