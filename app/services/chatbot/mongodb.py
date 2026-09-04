from pymongo import ASCENDING, DESCENDING

from app.core.mongodb import (
    mongo_client,
    chat_sessions_collection,
    chat_history_collection,
    pending_operations_collection,
)


async def initialize_chat_database():
    await mongo_client.admin.command(
        "ping"
    )

    await chat_sessions_collection.create_index(
        [
            ("user_id", ASCENDING),
            ("session_id", ASCENDING),
        ],
        unique=True,
    )

    await chat_sessions_collection.create_index(
        [
            ("user_id", ASCENDING),
            ("status", ASCENDING),
            ("modified_at", DESCENDING),
        ],
    )

    await chat_history_collection.create_index(
        [
            ("user_id", ASCENDING),
            ("session_id", ASCENDING),
            ("message_id", ASCENDING),
        ],
        unique=True,
    )

    await chat_history_collection.create_index(
        [
            ("user_id", ASCENDING),
            ("session_id", ASCENDING),
            ("created_at", ASCENDING),
        ],
    )
    await pending_operations_collection.create_index(
        [
            ("user_id", ASCENDING),
            ("session_id", ASCENDING),
        ],
        unique=True,
    )

    await pending_operations_collection.create_index(
        "expires_at",
        expireAfterSeconds=0,
    )

async def close_chat_database():
    mongo_client.close()