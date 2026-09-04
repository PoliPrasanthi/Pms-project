import json
import re
from datetime import datetime, timezone

import httpx
from pymongo import ReturnDocument

from app.core.mongodb import (
    chat_sessions_collection,
    chat_history_collection,
    chat_counters_collection,
)

from app.services.chatbot.tools.prompt import SYSTEM_PROMPT

from app.services.chatbot.graph.graph import (
    chat_graph,
)

from app.services.chatbot.graph.creation_graph import (
    run_creation_graph,
    get_pending_operation,
    delete_pending_operation,
)


PMS_PERMISSIONS_URL = (
    "http://127.0.0.1:8000/api/v1/chatbot/permissions"
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


async def get_user_permissions(
    access_token: str,
) -> dict:
    headers = {
        "Authorization": f"Bearer {access_token}",
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            PMS_PERMISSIONS_URL,
            headers=headers,
            timeout=60.0,
        )

        response.raise_for_status()

        result = response.json()

        return result.get(
            "permissions",
            {},
        )


async def get_or_create_session(
    user_id: int,
    access_token: str,
):
    session = await chat_sessions_collection.find_one(
        {
            "user_id": user_id,
            "status": "active",
        },
        sort=[
            ("modified_at", -1),
        ],
    )

    if session:
        last_activity = session.get(
            "modified_at",
            session.get("created_at"),
        )

        if last_activity:
            current_time = utc_now()

            if current_time.date() == last_activity.date():
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
                }
            },
        )

    permissions = await get_user_permissions(
        access_token=access_token,
    )

    print(
        "SESSION PERMISSIONS:",
        permissions,
    )

    new_session_id = await get_next_session_id(
        user_id,
    )

    now = utc_now()

    session = {
        "session_id": new_session_id,
        "user_id": user_id,
        "status": "active",
        "permissions": permissions,
        "created_at": now,
        "modified_at": now,
        "closed_at": None,
        "created_by": user_id,
        "modified_by": user_id,
    }

    await chat_sessions_collection.insert_one(
        session,
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
            ("message_id", -1),
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
    if not message.strip():
        return

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
        document,
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
        response_html,
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
        document,
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
        length=None,
    )


def history_to_messages(
    history: list,
) -> list:
    messages = []

    for item in history:
        role = item.get("role")
        message = item.get(
            "message",
            "",
        )

        response_data = (
            item.get("response_data")
            or {}
        )

        data = response_data.get(
            "data",
            [],
        )

        response_type = response_data.get(
            "response_type",
        )

        data_type = None

        if isinstance(data, dict):
            data_type = data.get(
                "type",
            )

        if role == "user":

            if message in {
                "Confirm creation",
                "Cancel creation",
            }:
                continue

            if message.startswith("{") and message.endswith("}"):
                try:
                    form_data = json.loads(message)

                    if isinstance(form_data, dict):
                        continue

                except json.JSONDecodeError:
                    pass

            messages.append(
                {
                    "role": "user",
                    "content": message,
                }
            )

        elif role == "agent":

            if response_type in {
                "form",
                "confirmation",
            }:
                continue

            if data_type in {
                "creation_cancelled",
                "creation_error",
                "task_creation_error",
                "project_creation_error",
            }:
                continue

            if data_type in {
                "task_created",
                "project_created",
            }:
                messages.append(
                    {
                        "role": "assistant",
                        "content": json.dumps(
                            response_data,
                            default=str,
                        ),
                    }
                )
                continue

            messages.append(
                {
                    "role": "assistant",
                    "content": message,
                }
            )

    return messages


async def update_session_activity(
    user_id: int,
    session_id: int,
):
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


async def handle_creation_action(
    *,
    user_id: int,
    session_id: int,
    access_token: str,
    current_user,
    action: str,
    form_data: dict | None = None,
):
    pending = await get_pending_operation(
        user_id=user_id,
        session_id=session_id,
    )

    if action == "cancel":

        await delete_pending_operation(
            user_id=user_id,
            session_id=session_id,
        )

        return {
            "response_type": "chat",
            "response": (
                "<p>Creation cancelled.</p>"
            ),
            "data": {
                "type": "creation_cancelled",
            },
        }

    if not pending:
        return {
            "response_type": "chat",
            "response": (
                "<p>There is no active "
                "creation workflow.</p>"
            ),
            "data": {},
        }

    entity_type = pending.get(
        "entity_type",
    )

    if not entity_type:
        return {
            "response_type": "chat",
            "response": (
                "<p>The pending creation "
                "workflow is invalid.</p>"
            ),
            "data": {},
        }

    arguments = pending.get(
        "arguments",
        {},
    )

    if action == "submit_form":

        return await run_creation_graph(
            user_id=user_id,
            session_id=session_id,
            current_user=current_user,
            access_token=access_token,
            entity_type=entity_type,
            action="submit_form",
            arguments=arguments,
            form_data=form_data or {},
        )

    if action in {
        "confirm",
        "approve",
        "yes",
    }:

        return await run_creation_graph(
            user_id=user_id,
            session_id=session_id,
            current_user=current_user,
            access_token=access_token,
            entity_type=entity_type,
            action="confirm",
            arguments=arguments,
            form_data={},
        )

    return {
        "response_type": "chat",
        "response": (
            "<p>Unsupported creation action.</p>"
        ),
        "data": {},
    }


async def run_agent(
    user_message: str,
    access_token: str,
    user_id: int,
    current_user,
    action: str | None = None,
    form_data: dict | None = None,
):
    session = await get_or_create_session(
        user_id=user_id,
        access_token=access_token,
    )

    session_id = session[
        "session_id"
    ]

    permissions = session.get(
        "permissions",
        {},
    )

    if action == "cancel":

        creation_result = await handle_creation_action(
            user_id=user_id,
            session_id=session_id,
            access_token=access_token,
            current_user=current_user,
            action="cancel",
            form_data=None,
        )

        await update_session_activity(
            user_id=user_id,
            session_id=session_id,
        )

        return {
            "session_id": session_id,
            **creation_result,
        }

    if action in {
        "submit_form",
        "confirm",
        "approve",
        "yes",
    }:

        if (
            action == "submit_form"
            and form_data
        ):
            await save_user_message(
                user_id=user_id,
                session_id=session_id,
                message=json.dumps(
                    form_data,
                    default=str,
                ),
            )

        elif action in {
            "confirm",
            "approve",
            "yes",
        }:
            await save_user_message(
                user_id=user_id,
                session_id=session_id,
                message="Confirm creation",
            )

        creation_result = await handle_creation_action(
            user_id=user_id,
            session_id=session_id,
            access_token=access_token,
            current_user=current_user,
            action=action,
            form_data=form_data,
        )

        response_type = creation_result.get(
            "response_type",
            "chat",
        )

        if response_type in {
            "form",
            "confirmation",
        }:

            await update_session_activity(
                user_id=user_id,
                session_id=session_id,
            )

            return {
                "session_id": session_id,
                **creation_result,
            }

        await save_agent_message(
            user_id=user_id,
            session_id=session_id,
            response_data=creation_result,
        )

        await update_session_activity(
            user_id=user_id,
            session_id=session_id,
        )

        return {
            "session_id": session_id,
            **creation_result,
        }

    if user_message.strip():

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
        history,
    )

    permission_context = (
        "\n\nUSER PERMISSIONS:\n"
        + json.dumps(
            permissions,
            default=str,
        )
    )

    messages = [
        {
            "role": "system",
            "content": (
                SYSTEM_PROMPT
                + permission_context
            ),
        },
        *history_messages,
    ]

    result = await chat_graph.ainvoke(
        {
            "messages": messages,
            "access_token": access_token,
            "current_user": current_user,
            "permissions": permissions,
        }
    )

    creation_request = result.get(
        "creation_request",
    )

    if creation_request:

        entity_type = creation_request.get(
            "entity_type",
        )

        arguments = creation_request.get(
            "arguments",
            {},
        )

        creation_result = await run_creation_graph(
            user_id=user_id,
            session_id=session_id,
            current_user=current_user,
            access_token=access_token,
            entity_type=entity_type,
            action=f"create_{entity_type}",
            arguments=arguments,
            form_data={},
        )

        response_type = creation_result.get(
            "response_type",
            "chat",
        )

        if response_type in {
            "form",
            "confirmation",
        }:

            await chat_history_collection.update_one(
                {
                    "user_id": user_id,
                    "session_id": session_id,
                    "role": "user",
                    "message": user_message,
                },
                {
                    "$set": {
                        "creation_context": True,
                    },
                },
            )

            await update_session_activity(
                user_id=user_id,
                session_id=session_id,
            )

            return {
                "session_id": session_id,
                **creation_result,
            }

        await save_agent_message(
            user_id=user_id,
            session_id=session_id,
            response_data=creation_result,
        )

        await update_session_activity(
            user_id=user_id,
            session_id=session_id,
        )

        return {
            "session_id": session_id,
            **creation_result,
        }

    final_response = result.get(
        "final_response",
        {
            "response_type": "chat",
            "response": (
                "<p>Unable to generate "
                "a response.</p>"
            ),
            "data": [],
        },
    )

    response_type = final_response.get(
        "response_type",
        "chat",
    )

    if response_type == "chat":
        await save_agent_message(
            user_id=user_id,
            session_id=session_id,
            response_data=final_response,
        )

    await update_session_activity(
        user_id=user_id,
        session_id=session_id,
    )

    return {
        "session_id": session_id,
        "response_type": response_type,
        "response": final_response.get(
            "response",
            "",
        ),
        "data": final_response.get(
            "data",
            [],
        ),
    }