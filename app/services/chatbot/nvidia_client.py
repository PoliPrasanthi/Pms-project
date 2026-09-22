import asyncio
import json

import httpx

from app.core.config import settings


# ============================================================
# NVIDIA HTTP CLIENT
# ============================================================

timeout = httpx.Timeout(
    connect=30.0,
    read=90.0,
    write=30.0,
    pool=30.0,
)

nvidia_client = httpx.AsyncClient(
    timeout=timeout,
)


# ============================================================
# NVIDIA CHAT COMPLETION
# ============================================================

async def chat_with_nvidia(
    messages: list,
    tools: list | None = None,
    json_mode: bool = False,
    tool_choice: dict | str | None = None,
):
    payload = {
        "model": settings.NVIDIA_MODEL,
        "messages": messages,
        "temperature": 0.2,
        "top_p": 0.95,
        "max_tokens": 1024,
        "stream": False,
    }

    # --------------------------------------------------------
    # TOOLS
    # --------------------------------------------------------

    if tools:
        payload["tools"] = tools

        # Keep parallel tool calls disabled.
        payload["parallel_tool_calls"] = False

        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

    # --------------------------------------------------------
    # JSON MODE
    # --------------------------------------------------------

    if json_mode:
        payload["response_format"] = {
            "type": "json_object"
        }

    # --------------------------------------------------------
    # HEADERS
    # --------------------------------------------------------

    headers = {
        "Authorization": (
            f"Bearer {settings.NVIDIA_API_KEY}"
        ),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    # --------------------------------------------------------
    # PAYLOAD VALIDATION
    # --------------------------------------------------------

    try:
        json.dumps(payload)
    except (TypeError, ValueError):
        raise

    # --------------------------------------------------------
    # RETRY CONFIGURATION
    # --------------------------------------------------------

    retryable_statuses = {
        500,
        502,
        503,
        504,
    }

    # --------------------------------------------------------
    # REQUEST
    # --------------------------------------------------------

    for attempt in range(1, 4):

        try:

            response = await nvidia_client.post(
                settings.NVIDIA_URL,
                headers=headers,
                json=payload,
            )

            response.raise_for_status()

            return response.json()

        except httpx.HTTPStatusError as exc:

            status = exc.response.status_code

            if (
                status in retryable_statuses
                and attempt < 3
            ):
                await asyncio.sleep(
                    2 ** attempt
                )
                continue

            raise

        except (
            httpx.ReadTimeout,
            httpx.ConnectTimeout,
            httpx.ConnectError,
        ):

            if attempt < 3:
                await asyncio.sleep(
                    2 ** attempt
                )
                continue

            raise

        except json.JSONDecodeError:
            raise

        except Exception:
            raise