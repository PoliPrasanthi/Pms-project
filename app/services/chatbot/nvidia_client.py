import asyncio
import json
import logging
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class NVIDIAClientError(Exception):
    """Base exception for errors raised by the NVIDIA API client."""


class NVIDIAServiceUnavailableError(NVIDIAClientError):
    """The NVIDIA service could not complete the request after retries."""


class NVIDIAAPIError(NVIDIAClientError):
    """NVIDIA returned a non-retryable HTTP error response."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class NVIDIAInvalidResponseError(NVIDIAClientError):
    """NVIDIA returned a response that could not be parsed as JSON."""


timeout = httpx.Timeout(
    connect=30.0,
    read=300.0,
    write=30.0,
    pool=30.0,
)

nvidia_client = httpx.AsyncClient(timeout=timeout)

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3
BASE_RETRY_DELAY_SECONDS = 2
MAX_RETRY_DELAY_SECONDS = 30
MAX_ERROR_BODY_LOG_CHARS = 1200


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Read Retry-After as seconds or an HTTP date, when provided."""
    value = response.headers.get("Retry-After")
    if not value:
        return None

    try:
        seconds = float(value)
        return max(0.0, min(seconds, MAX_RETRY_DELAY_SECONDS))
    except ValueError:
        pass

    try:
        retry_at = parsedate_to_datetime(value)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
        return max(0.0, min(seconds, MAX_RETRY_DELAY_SECONDS))
    except (TypeError, ValueError, OverflowError):
        return None


def _retry_delay(attempt: int, response: httpx.Response | None = None) -> float:
    """Use Retry-After when available; otherwise use bounded exponential backoff."""
    if response is not None:
        server_delay = _retry_after_seconds(response)
        if server_delay is not None:
            return server_delay

    return min(BASE_RETRY_DELAY_SECONDS ** attempt, MAX_RETRY_DELAY_SECONDS)


def _safe_error_details(response: httpx.Response) -> str:
    """Return a short provider error message for diagnostics, never request data."""
    try:
        body = response.text.strip()
    except Exception:
        return "<response body unavailable>"

    if not body:
        return "<empty response body>"

    # Error bodies usually contain a provider-generated reason (invalid parameter,
    # unsupported model option, context limit, etc.). Keep logs bounded.
    body = " ".join(body.split())
    if len(body) > MAX_ERROR_BODY_LOG_CHARS:
        body = body[:MAX_ERROR_BODY_LOG_CHARS] + "... [truncated]"
    return body


async def chat_with_nvidia(
    messages: list,
    tools: list | None = None,
    json_mode: bool = False,
    tool_choice: dict | str | None = None,
    max_tokens: int = 1024,
):
    """Call NVIDIA chat completions with bounded retries and useful error logging."""
    payload = {
        "model": settings.NVIDIA_MODEL,
        "messages": messages,
        "temperature": 0.2,
        "top_p": 0.95,
        "max_tokens": max_tokens,
        "stream": False,
    }

    if tools:
        payload["tools"] = tools
        payload["parallel_tool_calls"] = False
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    headers = {
        "Authorization": f"Bearer {settings.NVIDIA_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    try:
        json.dumps(payload)
    except (TypeError, ValueError) as exc:
        logger.exception("NVIDIA request payload is not JSON serializable")
        raise ValueError("The NVIDIA request payload is invalid.") from exc

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            logger.debug(
                "Sending NVIDIA chat completion request (attempt %s/%s; model=%s; "
                "json_mode=%s; tools=%s; max_tokens=%s)",
                attempt,
                MAX_ATTEMPTS,
                settings.NVIDIA_MODEL,
                json_mode,
                bool(tools),
                max_tokens,
            )

            response = await nvidia_client.post(
                settings.NVIDIA_URL,
                headers=headers,
                json=payload,
            )

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                is_retryable = status_code in RETRYABLE_STATUS_CODES

                logger.warning(
                    "NVIDIA returned HTTP %s (attempt %s/%s; retryable=%s; "
                    "content_type=%s; provider_error=%s)",
                    status_code,
                    attempt,
                    MAX_ATTEMPTS,
                    is_retryable,
                    exc.response.headers.get("content-type", "unknown"),
                    _safe_error_details(exc.response),
                )

                if is_retryable and attempt < MAX_ATTEMPTS:
                    delay = _retry_delay(attempt, exc.response)
                    logger.info("Retrying NVIDIA request in %.1f seconds", delay)
                    await asyncio.sleep(delay)
                    continue

                if is_retryable:
                    raise NVIDIAServiceUnavailableError(
                        "The NVIDIA AI service is temporarily unavailable."
                    ) from exc

                raise NVIDIAAPIError(
                    f"NVIDIA API request failed with HTTP {status_code}.",
                    status_code=status_code,
                ) from exc

            try:
                return response.json()
            except (json.JSONDecodeError, ValueError) as exc:
                logger.error(
                    "NVIDIA returned a successful HTTP status but an invalid JSON "
                    "response (status=%s)",
                    response.status_code,
                )
                raise NVIDIAInvalidResponseError(
                    "The AI service returned an invalid response."
                ) from exc

        except httpx.RequestError as exc:
            logger.warning(
                "NVIDIA network request failed (attempt %s/%s; error_type=%s)",
                attempt,
                MAX_ATTEMPTS,
                type(exc).__name__,
            )

            if attempt < MAX_ATTEMPTS:
                delay = _retry_delay(attempt)
                logger.info("Retrying NVIDIA request in %.1f seconds", delay)
                await asyncio.sleep(delay)
                continue

            raise NVIDIAServiceUnavailableError(
                "The NVIDIA AI service could not be reached."
            ) from exc

        except NVIDIAClientError:
            raise

        except Exception:
            logger.exception("Unexpected error while calling NVIDIA API")
            raise

    raise NVIDIAServiceUnavailableError(
        "The NVIDIA AI service is temporarily unavailable."
    )
