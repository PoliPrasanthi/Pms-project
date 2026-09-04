from fastapi import APIRouter, Depends, Request

from app.core.security import get_current_user

from app.schemas.chatbot import (
    ChatRequest,
    ChatResponse,
)

from app.services.chatbot.agent import run_agent


router = APIRouter()


def get_access_token(
    request: Request,
) -> str:
    access_token = request.headers.get(
        "Authorization",
        "",
    )

    if access_token.startswith("Bearer "):
        access_token = access_token[7:]

    return access_token


@router.post(
    "/chat",
    response_model=ChatResponse,
)
async def chat(
    request: Request,
    chat_request: ChatRequest,
    current_user=Depends(get_current_user),
):
    access_token = get_access_token(request)

    return await run_agent(
        user_message=chat_request.message,
        access_token=access_token,
        user_id=current_user.id,
        current_user=current_user,
        action=chat_request.action,
        form_data=chat_request.form_data,
    )

@router.get("/permissions")
async def get_my_permissions(
    current_user=Depends(get_current_user),
):
    permissions = current_user.role.permissions or {}

    if isinstance(permissions, str):
        import json

        try:
            permissions = json.loads(permissions)
        except Exception:
            permissions = {}

    return {
        "permissions": permissions
    }