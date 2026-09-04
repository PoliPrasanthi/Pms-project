from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import settings


mongo_client = AsyncIOMotorClient(
    settings.MONGODB_URL
)

mongo_db = mongo_client[
    settings.MONGODB_DATABASE
]

chat_sessions_collection = mongo_db[
    "chat_sessions"
]

chat_history_collection = mongo_db[
    "chat_history"
]

chat_counters_collection = mongo_db[
    "chat_counters"
]
pending_operations_collection = mongo_db[
    "pending_operations"
]