import httpx

from app.core.config import settings


async def chat_with_nvidia(
    messages: list,
    tools: list | None = None,
):


    payload = {
        "model": settings.NVIDIA_MODEL,
        "messages": messages,
        "temperature": 1,
        "top_p": 0.95,
        "max_tokens": 8192,
        "stream": False,
    }

    if tools:
        payload["tools"] = tools

    headers = {
        "Authorization": f"Bearer {settings.NVIDIA_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient() as client:


        response = await client.post(
            settings.NVIDIA_URL,
            headers=headers,
            json=payload,
            timeout=120.0,
        )

        response.raise_for_status()

        return response.json()