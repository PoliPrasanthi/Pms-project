import asyncio
import os
import time

from openai import AsyncOpenAI
from app.core.config import settings


async def main():
    print("Model:", settings.NVIDIA_MODEL)
    print("API key configured:", bool(settings.NVIDIA_API_KEY))

    client = AsyncOpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=settings.NVIDIA_API_KEY,
        timeout=90.0,
        max_retries=0,
    )

    start = time.perf_counter()

    try:
        response = await client.chat.completions.create(
            model="nvidia/nemotron-3-ultra-550b-a55b",
            messages=[
                {
                    "role": "user",
                    "content": "Reply with exactly: Hello",
                }
            ],
            max_tokens=20,
            temperature=0.2,
        )

        print("Response time:", round(time.perf_counter() - start, 2), "seconds")
        print("Response:", response.choices[0].message.content)

    except Exception as exc:
        print("Elapsed:", round(time.perf_counter() - start, 2), "seconds")
        print("Error type:", type(exc).__name__)
        print("Error:", str(exc)[:1500])

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())