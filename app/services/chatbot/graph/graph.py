import json

from langgraph.graph import (
    StateGraph,
    START,
    END,
)

from app.services.chatbot.graph.state import ChatState

from app.services.chatbot.tools.prompt import (
    FINAL_SYSTEM_PROMPT,
)

from app.services.chatbot.nvidia_client import (
    chat_with_nvidia,
)

from app.services.chatbot.tools import (
    PROJECT_TOOLS,
    TOOL_FUNCTIONS,
)


TOOLS = PROJECT_TOOLS


async def llm_node(
    state: ChatState,
):
    response = await chat_with_nvidia(
        messages=state["messages"],
        tools=TOOLS,
    )

    message = response.get(
        "choices",
        [{}],
    )[0].get(
        "message",
        {},
    )

    return {
        "messages": (
            state["messages"]
            + [message]
        )
    }


def route_after_llm(
    state: ChatState,
):
    last_message = state["messages"][-1]

    tool_calls = last_message.get(
        "tool_calls",
        [],
    )

    if tool_calls:
        return "tools"

    return "final"


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

        tool_function = TOOL_FUNCTIONS.get(
            tool_name
        )

        if not tool_function:

            result = {
                "error": (
                    f"Unknown tool: {tool_name}"
                )
            }

        else:

            result = await tool_function(
                state["access_token"],
                arguments,
            )

        print(
            f"\nTOOL: {tool_name}"
        )

        print(
            f"ARGUMENTS: {arguments}"
        )

        tool_messages.append(
            {
                "role": "tool",
                "content": json.dumps(
                    result,
                    default=str,
                ),
                "tool_call_id": tool_call.get(
                    "id"
                ),
            }
        )

    return {
        "messages": (
            messages
            + tool_messages
        )
    }


async def final_node(
    state: ChatState,
):
    user_message = ""

    for message in state["messages"]:

        if message.get(
            "role"
        ) == "user":

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

            final_messages.append(
                message
            )

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

    content = final_message.get(
        "content",
        "",
    ) or ""

    try:

        result = json.loads(
            content
        )

        if not isinstance(
            result,
            dict,
        ):

            result = {
                "response": content,
                "data": {},
            }

    except json.JSONDecodeError:

        result = {
            "response": content,
            "data": {},
        }

    return {
        "final_response": result
    }


def build_chat_graph():

    graph = StateGraph(
        ChatState
    )

    graph.add_node(
        "llm",
        llm_node
    )

    graph.add_node(
        "tools",
        tool_node
    )

    graph.add_node(
        "final",
        final_node
    )

    graph.add_edge(
        START,
        "llm"
    )

    graph.add_conditional_edges(
        "llm",
        route_after_llm,
        {
            "tools": "tools",
            "final": "final",
        },
    )

    graph.add_edge(
        "tools",
        "llm"
    )

    graph.add_edge(
        "final",
        END
    )

    return graph.compile()


chat_graph = build_chat_graph()