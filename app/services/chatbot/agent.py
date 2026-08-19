import json

from app.services.chatbot.tools.prompt import SYSTEM_PROMPT
from app.services.chatbot.ollama_client import chat_with_ollama
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

    response = await chat_with_ollama(
        messages=messages,
        tools=TOOLS,
    )

    message = response.get("message", {})

    print("\nFIRST LLM RESPONSE:")
    print(response)

    tool_calls = message.get("tool_calls", [])

    if not tool_calls:

        return {
            "response": message.get("content", "")
        }

    messages.append(message)


    for tool_call in tool_calls:

        function = tool_call.get(
            "function",
            {}
        )

        tool_name = function.get("name")

        arguments = function.get(
            "arguments",
            {}
        )

        print("\nTOOL SELECTED:")
        print(tool_name)

        print("\nTOOL ARGUMENTS:")
        print(arguments)


        tool_function = TOOL_FUNCTIONS.get(tool_name)

        if tool_function:

            result = await tool_function(
                access_token
            )

        else:

            result = {
                "error": f"Unknown tool: {tool_name}"
            }

        print("\nTOOL RESULT:")
        print(result)



        messages.append(
            {
                "role": "tool",
                "content": json.dumps(result),
            }
        )


    final_response = await chat_with_ollama(
        messages=messages,
    )

    print("\nFINAL LLM RESPONSE:")
    print(final_response)

    final_message = final_response.get(
        "message",
        {}
    )

    return {
        "response": final_message.get(
            "content",
            ""
        )
    }