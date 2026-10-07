import asyncio
import httpx

from app.core.config import settings


async def test_nvidia():

    url = settings.NVIDIA_URL

    headers = {
        "Authorization": f"Bearer {settings.NVIDIA_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    payload = {
        "model": settings.NVIDIA_MODEL,
        "messages": [
            {
                "role": "user",
                "content": "Hello. Reply with exactly: NVIDIA TEST SUCCESS"
            }
        ],
        "temperature": 0.2,
        "top_p": 0.95,
        "max_tokens": 100,
        "stream": False,
    }

    print("=" * 60)
    print("NVIDIA BASIC TEST")
    print("=" * 60)

    print("URL:", url)
    print("MODEL:", settings.NVIDIA_MODEL)

    async with httpx.AsyncClient(timeout=120.0) as client:

        response = await client.post(
            url,
            headers=headers,
            json=payload,
        )

        print("STATUS:", response.status_code)
        print("RESPONSE:")
        print(response.text)

        response.raise_for_status()


if __name__ == "__main__":
    asyncio.run(test_nvidia())