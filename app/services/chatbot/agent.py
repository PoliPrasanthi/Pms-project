import json

from app.services.chatbot.tools.prompt import (
    SYSTEM_PROMPT,
    FINAL_SYSTEM_PROMPT,
)

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

    while True:

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
            break

        messages.append(
            message
        )

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
                    "error": f"Unknown tool: {tool_name}"
                }

            else:

                result = await tool_function(
                    access_token,
                    arguments
                )

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

    for message in messages[2:]:
        final_messages.append(
            message
        )

    final_response = await chat_with_nvidia(
        messages=final_messages,
        json_mode=True,
    )

    final_message = final_response.get(
        "choices",
        [{}]
    )[0].get(
        "message",
        {}
    )

    content = final_message.get(
        "content",
        ""
    ) or ""

    try:

        result = json.loads(
            content
        )

        if not isinstance(result, dict):

            return {
                "response": content,
                "data": {}
            }

        response_value = result.get(
            "response",
            ""
        )

        data_value = result.get(
            "data",
            {}
        )

        if isinstance(
            response_value,
            str
        ):

            nested_content = response_value.strip()

            if (
                nested_content.startswith("{")
                and
                nested_content.endswith("}")
            ):

                try:

                    nested_result = json.loads(
                        nested_content
                    )

                    if isinstance(
                        nested_result,
                        dict
                    ):

                        response_value = nested_result.get(
                            "response",
                            response_value
                        )

                        data_value = nested_result.get(
                            "data",
                            data_value
                        )

                except json.JSONDecodeError:
                    pass

        return {
            "response": response_value,
            "data": data_value
        }

    except json.JSONDecodeError:

        return {
            "response": content,
            "data": {}
        }