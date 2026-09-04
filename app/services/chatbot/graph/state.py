from typing import Any

from typing_extensions import TypedDict


class ChatState(TypedDict, total=False):
    messages: list
    access_token: str
    current_user: Any
    session_id: int
    user_id: Any
    permissions: dict
    creation_request: dict
    final_response: dict