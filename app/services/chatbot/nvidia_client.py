import asyncio
import json

import httpx

from app.core.config import settings


# ============================================================
# NVIDIA HTTP CLIENT
# ============================================================

timeout = httpx.Timeout(
    connect=30.0,
    read=300.0,
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
    max_tokens: int = 1024,
):
    payload = {
        "model": settings.NVIDIA_MODEL,
        "messages": messages,
        "temperature": 0.2,
        "top_p": 0.95,
        "max_tokens": max_tokens,
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

    max_attempts = 3

    # --------------------------------------------------------
    # REQUEST
    # --------------------------------------------------------

    for attempt in range(1, max_attempts + 1):

        try:

            print(
                f"[NVIDIA] Request attempt "
                f"{attempt}/{max_attempts}"
            )

            print(
                f"[NVIDIA] Max tokens: {max_tokens}"
            )

            response = await nvidia_client.post(
                settings.NVIDIA_URL,
                headers=headers,
                json=payload,
            )

            # ------------------------------------------------
            # ERROR RESPONSE LOGGING
            # ------------------------------------------------

            if response.status_code >= 400:

                print("\n" + "=" * 70)
                print("[NVIDIA] ERROR RESPONSE")
                print("Attempt:", attempt)
                print("Status:", response.status_code)
                print("Response:", response.text)
                print("=" * 70 + "\n")

            # ------------------------------------------------
            # SUCCESS
            # ------------------------------------------------

            response.raise_for_status()

            return response.json()

        # ----------------------------------------------------
        # HTTP STATUS ERROR
        # ----------------------------------------------------

        except httpx.HTTPStatusError as exc:

            status = exc.response.status_code

            print(
                f"[NVIDIA] HTTP error "
                f"{status} on attempt "
                f"{attempt}/{max_attempts}"
            )

            # Retry temporary NVIDIA/server errors
            if (
                status in retryable_statuses
                and attempt < max_attempts
            ):

                delay = 2 ** attempt

                print(
                    f"[NVIDIA] Retrying in "
                    f"{delay} seconds..."
                )

                await asyncio.sleep(delay)

                continue

            # ------------------------------------------------
            # FINAL FAILURE
            # ------------------------------------------------

            print(
                "[NVIDIA] Request failed after "
                f"{attempt} attempt(s)."
            )

            raise

        # ----------------------------------------------------
        # TIMEOUT / CONNECTION ERRORS
        # ----------------------------------------------------

        except (
            httpx.ReadTimeout,
            httpx.ConnectTimeout,
            httpx.ConnectError,
        ) as exc:

            print(
                f"[NVIDIA] Connection/timeout error "
                f"on attempt "
                f"{attempt}/{max_attempts}: "
                f"{exc}"
            )

            if attempt < max_attempts:

                delay = 2 ** attempt

                print(
                    f"[NVIDIA] Retrying in "
                    f"{delay} seconds..."
                )

                await asyncio.sleep(delay)

                continue

            print(
                "[NVIDIA] Request failed after "
                f"{attempt} attempt(s)."
            )

            raise

        # ----------------------------------------------------
        # INVALID JSON
        # ----------------------------------------------------

        except json.JSONDecodeError as exc:

            print(
                "[NVIDIA] Invalid JSON response:",
                exc,
            )

            raise

        # ----------------------------------------------------
        # UNKNOWN ERROR
        # ----------------------------------------------------

        except Exception as exc:

            print(
                "[NVIDIA] Unexpected error:",
                repr(exc),
            )

            raise