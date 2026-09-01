import re

from datetime import datetime, timezone

from pymongo import ReturnDocument

from app.services.chatbot.tools.prompt import SYSTEM_PROMPT
from app.services.chatbot.graph.graph import chat_graph

from app.core.mongodb import (
    chat_sessions_collection,
    chat_history_collection,
    chat_counters_collection,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def html_to_text(
    html: str,
) -> str:
    return re.sub(
        r"<[^>]+>",
        "",
        html,
    ).strip()


async def get_next_session_id(
    user_id: int,
) -> int:
    result = await chat_counters_collection.find_one_and_update(
        {
            "_id": f"user_{user_id}_session",
            "user_id": user_id,
        },
        {
            "$inc": {
                "sequence": 1,
            }
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )

    return result["sequence"]


async def get_or_create_session(
    user_id: int,
):
    session = await chat_sessions_collection.find_one(
        {
            "user_id": user_id,
            "status": "active",
        },
        sort=[
            ("modified_at", -1)
        ],
    )

    if session:

        last_activity = session.get(
            "modified_at",
            session.get("created_at"),
        )

        if last_activity:

            current_time = utc_now()

            if (
                current_time.date()
                == last_activity.date()
            ):
                return session

        now = utc_now()

        await chat_sessions_collection.update_one(
            {
                "user_id": user_id,
                "session_id": session["session_id"],
                "status": "active",
            },
            {
                "$set": {
                    "status": "closed",
                    "closed_at": now,
                    "modified_at": now,
                    "modified_by": user_id,
                }
            },
        )

    new_session_id = await get_next_session_id(
        user_id
    )

    now = utc_now()

    session = {
        "session_id": new_session_id,
        "user_id": user_id,
        "status": "active",
        "created_at": now,
        "modified_at": now,
        "closed_at": None,
        "created_by": user_id,
        "modified_by": user_id,
    }

    await chat_sessions_collection.insert_one(
        session
    )

    return session


async def get_next_message_id(
    user_id: int,
    session_id: int,
) -> int:
    last_message = await chat_history_collection.find_one(
        {
            "user_id": user_id,
            "session_id": session_id,
        },
        sort=[
            ("message_id", -1)
        ],
    )

    if not last_message:
        return 1

    return last_message["message_id"] + 1


async def save_user_message(
    user_id: int,
    session_id: int,
    message: str,
):
    message_id = await get_next_message_id(
        user_id=user_id,
        session_id=session_id,
    )

    now = utc_now()

    document = {
        "message_id": message_id,
        "user_id": user_id,
        "session_id": session_id,
        "role": "user",
        "message": message,
        "response_data": None,
        "created_at": now,
        "modified_at": now,
        "created_by": user_id,
        "modified_by": user_id,
    }

    await chat_history_collection.insert_one(
        document
    )


async def save_agent_message(
    user_id: int,
    session_id: int,
    response_data: dict,
):
    message_id = await get_next_message_id(
        user_id=user_id,
        session_id=session_id,
    )

    response_html = response_data.get(
        "response",
        "",
    )

    message = html_to_text(
        response_html
    )

    now = utc_now()

    document = {
        "message_id": message_id,
        "user_id": user_id,
        "session_id": session_id,
        "role": "agent",
        "message": message,
        "response_data": response_data,
        "created_at": now,
        "modified_at": now,
        "created_by": user_id,
        "modified_by": user_id,
    }

    await chat_history_collection.insert_one(
        document
    )


async def load_history(
    user_id: int,
    session_id: int,
) -> list:
    cursor = chat_history_collection.find(
        {
            "user_id": user_id,
            "session_id": session_id,
        },
        {
            "_id": 0,
            "message_id": 1,
            "user_id": 1,
            "session_id": 1,
            "role": 1,
            "message": 1,
            "response_data": 1,
        },
    ).sort(
        "message_id",
        1,
    )

    return await cursor.to_list(
        length=None
    )


def history_to_messages(
    history: list,
) -> list:
    messages = []

    for item in history:

        role = item.get(
            "role"
        )

        message = item.get(
            "message",
            "",
        )

        if role == "user":

            messages.append(
                {
                    "role": "user",
                    "content": message,
                }
            )

        elif role == "agent":

            messages.append(
                {
                    "role": "assistant",
                    "content": message,
                }
            )

    return messages


async def run_agent(
    user_message: str,
    access_token: str,
    user_id: int,
):
    session = await get_or_create_session(
        user_id=user_id,
    )

    session_id = session["session_id"]

    await save_user_message(
        user_id=user_id,
        session_id=session_id,
        message=user_message,
    )

    history = await load_history(
        user_id=user_id,
        session_id=session_id,
    )

    history_messages = history_to_messages(
        history
    )

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
        *history_messages,
    ]

    result = await chat_graph.ainvoke(
        {
            "messages": messages,
            "access_token": access_token,
        }
    )

    final_response = result.get(
        "final_response",
        {
            "response": (
                "<p>Unable to generate a response.</p>"
            ),
            "data": {},
        },
    )

    await save_agent_message(
        user_id=user_id,
        session_id=session_id,
        response_data=final_response,
    )

    now = utc_now()

    await chat_sessions_collection.update_one(
        {
            "user_id": user_id,
            "session_id": session_id,
            "status": "active",
        },
        {
            "$set": {
                "modified_at": now,
                "modified_by": user_id,
            }
        },
    )

    return {
        "session_id": session_id,
        "response": final_response.get(
            "response",
            "",
        ),
        "data": final_response.get(
            "data",
            {},
        ),
    }