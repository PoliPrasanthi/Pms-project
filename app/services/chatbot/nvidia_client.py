import time
import httpx

from app.core.config import settings


async def chat_with_nvidia(
    messages: list,
    tools: list | None = None,
    json_mode: bool = False,
):
    payload = {
        "model": settings.NVIDIA_MODEL,
        "messages": messages,
        "temperature": 0.2,
        "top_p": 0.95,
        "max_tokens": 2048,
        "stream": False,
    }

    if tools:
        payload["tools"] = tools
        payload["parallel_tool_calls"] = True

    if json_mode:
        payload["response_format"] = {
            "type": "json_object"
        }

    headers = {
        "Authorization": f"Bearer {settings.NVIDIA_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    timeout = httpx.Timeout(
        connect=10.0,
        read=300.0,
        write=30.0,
        pool=10.0,
    )

    async with httpx.AsyncClient(
        timeout=timeout
    ) as client:

        response = await client.post(
            settings.NVIDIA_URL,
            headers=headers,
            json=payload,
        )

        response.raise_for_status()

        return response.json()