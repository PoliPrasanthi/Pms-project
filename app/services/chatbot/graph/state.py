from typing import Any

from typing_extensions import TypedDict


class ChatState(TypedDict, total=False):
    messages: list
    access_token: str
    current_user: Any
    user_id: Any
    session_id: int
    permissions: dict

    action: str | None
    form_data: dict | None

    creation_request: dict
    final_response: dict
    current_user_profile: dict