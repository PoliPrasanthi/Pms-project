from fastapi import APIRouter, Depends, Request

from app.core.security import get_current_user
from app.schemas.chatbot import ChatRequest
from app.services.chatbot.agent import run_agent


router = APIRouter()


@router.post("/chat")
async def chat(
    request: Request,
    chat_request: ChatRequest,
    current_user=Depends(get_current_user),
):
    access_token = request.headers.get(
        "Authorization",
        ""
    )

    if access_token.startswith("Bearer "):
        access_token = access_token[7:]

    return await run_agent(
        user_message=chat_request.message,
        access_token=access_token,
    )