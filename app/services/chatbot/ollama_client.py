import httpx

from app.core.config import settings


async def chat_with_ollama(
    messages: list,
    tools: list | None = None,
):
    payload = {
        "model": settings.OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
    }

    if tools:
        payload["tools"] = tools

    async with httpx.AsyncClient() as client:
        response = await client.post(
            settings.OLLAMA_URL,
            json=payload,
            timeout=120.0,
        )

        response.raise_for_status()

        return response.json()