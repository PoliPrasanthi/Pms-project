import asyncio
import json

from app.services.chatbot.nvidia_client import chat_with_nvidia
from app.services.chatbot.tools import PROJECT_TOOLS
from app.services.chatbot.tools.prompt import SYSTEM_PROMPT


async def main():

    permissions = {
        "task-create": True,
        "task-view": True,
        "proj-create": True,
        "proj-view": True,
    }

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
        {
            "role": "system",
            "content": (
                "SESSION PERMISSIONS:\n"
                + json.dumps(permissions)
            ),
        },
        {
            "role": "user",
            "content": "Who am I?",
        },
    ]

    print("TOOLS COUNT:", len(PROJECT_TOOLS))
    print("MESSAGE COUNT:", len(messages))
    print("Calling NVIDIA...")

    result = await chat_with_nvidia(
        messages=messages,
        tools=PROJECT_TOOLS,
    )

    print("\nRESULT:")
    print(result)


asyncio.run(main())