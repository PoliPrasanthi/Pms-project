from typing_extensions import TypedDict


class ChatState(TypedDict, total=False):
    messages: list
    access_token: str
    final_response: dict