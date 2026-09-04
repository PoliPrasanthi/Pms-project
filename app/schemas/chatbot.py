from typing import Any, Literal

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str = ""

    action: Literal[
        "submit_form",
        "confirm",
        "cancel",
        "approve",
        "yes",
    ] | None = None

    form_data: dict[str, Any] | None = None


class ChatResponse(BaseModel):
    session_id: int
    response_type: Literal[
        "chat",
        "form",
        "confirmation",
    ]
    response: str
    data: Any