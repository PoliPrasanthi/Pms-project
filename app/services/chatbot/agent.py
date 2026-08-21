import json

from app.services.chatbot.tools.prompt import SYSTEM_PROMPT
from app.services.chatbot.nvidia_client import chat_with_nvidia
from app.services.chatbot.tools import (
    PROJECT_TOOLS,
    TOOL_FUNCTIONS,
)


TOOLS = PROJECT_TOOLS


async def run_agent(
    user_message: str,
    access_token: str,
):

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": user_message,
        },
    ]

    response = await chat_with_nvidia(
        messages=messages,
        tools=TOOLS,
    )

    message = response.get(
        "choices",
        [{}]
    )[0].get(
        "message",
        {}
    )

    tool_calls = message.get(
        "tool_calls",
        []
    )

    if not tool_calls:
        print("No tool calls found in the response.")
        return {
            "response": message.get(
                "content",
                ""
            )
        }
    messages.append(message)

    for tool_call in tool_calls:

        function = tool_call.get(
            "function",
            {}
        )

        tool_name = function.get(
            "name"
        )

        arguments = function.get(
            "arguments",
            {}
        )

        if isinstance(arguments, str):

            try:
                arguments = json.loads(arguments)

            except json.JSONDecodeError:
                arguments = {}

        tool_function = TOOL_FUNCTIONS.get(
            tool_name
        )

        if not tool_function:

            result = {
                "error": f"Unknown tool: {tool_name}"
            }

        else:
            result = await tool_function(
                access_token,
                arguments
            )
        print(f"\nTOOL {tool_call}:")
        print(f"NAME      : {tool_name}")
        print(f"ARGUMENTS : {arguments}")

        messages.append(
            {
                "role": "tool",
                "content": json.dumps(
                    result,
                    default=str
                ),
                "tool_call_id": tool_call.get(
                    "id"
                ),
            }
        )

    final_response = await chat_with_nvidia(
        messages=messages,
    )

    final_message = final_response.get(
        "choices",
        [{}]
    )[0].get(
        "message",
        {}
    )
    return {
        "response": final_message.get(
            "content",
            ""
        ) or ""
    }