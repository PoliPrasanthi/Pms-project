from datetime import datetime, timedelta, timezone
from typing import Any

from langgraph.graph import (
    StateGraph,
    START,
    END,
)

from typing_extensions import TypedDict

from app.core.mongodb import pending_operations_collection

from app.services.chatbot.tools.project_tools import (
    REQUIRED_TASK_FIELDS,
    REQUIRED_PROJECT_FIELDS,
    DEFAULT_TASK_VALUES,
    DEFAULT_PROJECT_VALUES,
    create_task,
    create_project,
)


class CreationState(TypedDict, total=False):
    access_token: str
    current_user: Any
    session_id: int
    user_id: Any

    entity_type: str
    action: str | None

    arguments: dict[str, Any]
    form_data: dict[str, Any] | None

    pending: dict[str, Any] | None
    missing_fields: list[str]

    response_type: str
    response: str
    data: dict[str, Any]

    result: dict[str, Any]


CREATION_CONFIG = {
    "task": {
        "required_fields": REQUIRED_TASK_FIELDS,
        "name": "task",
    },
    "project": {
        "required_fields": REQUIRED_PROJECT_FIELDS,
        "name": "project",
    },
}


async def get_pending_operation(
    user_id: Any,
    session_id: int,
):
    return await pending_operations_collection.find_one(
        {
            "user_id": user_id,
            "session_id": session_id,
        }
    )


async def save_pending_operation(
    user_id: Any,
    session_id: int,
    operation: str,
    entity_type: str,
    arguments: dict[str, Any],
    status: str,
):
    now = datetime.now(timezone.utc)

    await pending_operations_collection.update_one(
        {
            "user_id": user_id,
            "session_id": session_id,
        },
        {
            "$set": {
                "user_id": user_id,
                "session_id": session_id,
                "operation": operation,
                "entity_type": entity_type,
                "arguments": arguments,
                "status": status,
                "updated_at": now,
                "expires_at": (
                    now + timedelta(minutes=10)
                ),
            },
            "$setOnInsert": {
                "created_at": now,
            },
        },
        upsert=True,
    )


async def delete_pending_operation(
    user_id: Any,
    session_id: int,
):
    result = await pending_operations_collection.delete_one(
        {
            "user_id": user_id,
            "session_id": session_id,
        }
    )

    return result.deleted_count


def get_missing_fields(
    arguments: dict[str, Any],
    required_fields: list[str],
) -> list[str]:
    missing = []

    for field in required_fields:
        value = arguments.get(field)

        if value is None:
            missing.append(field)

        elif (
            isinstance(value, str)
            and not value.strip()
        ):
            missing.append(field)

    return missing


async def load_pending_node(
    state: CreationState,
) -> CreationState:
    pending = await get_pending_operation(
        user_id=state["user_id"],
        session_id=state["session_id"],
    )

    return {
        "pending": pending,
    }


def merge_data_node(
    state: CreationState,
) -> CreationState:
    pending = state.get("pending") or {}

    arguments = dict(
        pending.get("arguments")
        or state.get("arguments")
        or {}
    )

    form_data = state.get("form_data") or {}

    for key, value in form_data.items():
        if value is not None:
            arguments[key] = value

    entity_type = state["entity_type"]

    if entity_type == "task":
        if "Start_date" in arguments:
            arguments["start_date"] = arguments.pop(
                "Start_date"
            )

        for key, value in DEFAULT_TASK_VALUES.items():
            current_value = arguments.get(key)

            if current_value is None or (
                isinstance(current_value, str)
                and not current_value.strip()
            ):
                arguments[key] = value
    elif entity_type == "project":
        if "Start_date" in arguments:
            arguments["start_date"] = arguments.pop(
                "Start_date"
            )
        for key, value in DEFAULT_PROJECT_VALUES.items():
            current_value = arguments.get(key)

            if current_value is None or (
                isinstance(current_value, str)
                and not current_value.strip()
            ):
                arguments[key] = value

    return {
        "arguments": arguments,
    }


def validate_node(
    state: CreationState,
) -> CreationState:
    entity_type = state["entity_type"]

    config = CREATION_CONFIG.get(
        entity_type
    )

    if not config:
        return {
            "missing_fields": []
        }

    missing = get_missing_fields(
        state.get(
            "arguments",
            {},
        ),
        config["required_fields"],
    )

    return {
        "missing_fields": missing,
    }


def route_after_validation(
    state: CreationState,
) -> str:
    if state.get("missing_fields"):
        return "form"

    action = (
        state.get("action")
        or ""
    ).lower().strip()

    if action == "submit_form":
        return "confirmation"

    if action in {
        "confirm",
        "approve",
        "yes",
    }:
        return "create"

    return "confirmation"


async def build_form_node(
    state: CreationState,
) -> CreationState:
    arguments = state.get(
        "arguments",
        {},
    )

    entity_type = state["entity_type"]

    await save_pending_operation(
        user_id=state["user_id"],
        session_id=state["session_id"],
        operation="create",
        entity_type=entity_type,
        arguments=arguments,
        status="awaiting_input",
    )

    return {
        "response_type": "form",
        "response": (
            "<p>Please provide the required "
            "details.</p>"
        ),
        "data": {
            "type": (
                f"create_{entity_type}_form"
            ),
            "form_type": (
                f"create_{entity_type}"
            ),
        },
    }


async def build_confirmation_node(
    state: CreationState,
) -> CreationState:
    arguments = state.get(
        "arguments",
        {},
    )

    entity_type = state["entity_type"]

    await save_pending_operation(
        user_id=state["user_id"],
        session_id=state["session_id"],
        operation="create",
        entity_type=entity_type,
        arguments=arguments,
        status="awaiting_confirmation",
    )

    return {
        "response_type": "confirmation",
        "response": (
            f"<p>Please confirm that you want "
            f"to create this {entity_type}.</p>"
        ),
        "data": {
            "type": (
                f"{entity_type}_confirmation"
            ),
            "action": (
                f"create_{entity_type}"
            ),
            "requires_confirmation": True,
            entity_type: arguments,
        },
    }


async def create_node(
    state: CreationState,
) -> CreationState:
    entity_type = state["entity_type"]

    arguments = state.get(
        "arguments",
        {},
    )

    if entity_type == "task":

        result = await create_task(
            access_token=state["access_token"],
            current_user=state["current_user"],
            arguments=arguments,
        )

    elif entity_type == "project":

        result = await create_project(
            access_token=state["access_token"],
            current_user=state["current_user"],
            arguments=arguments,
        )

    else:

        result = {
            "success": False,
            "error": (
                f"Creation for '{entity_type}' "
                "is not implemented yet."
            ),
        }

    if result.get("success") is True:

        await delete_pending_operation(
            user_id=state["user_id"],
            session_id=state["session_id"],
        )


        return {
            "response_type": "chat",
            "response": (
                f"<p>{entity_type.capitalize()} "
                "created successfully.</p>"
            ),
            "data": {
                "type": (
                    f"{entity_type}_created"
                ),
                # entity_type: result.get(
                #     "data",
                #     {},
                # ),
            },
            # "result": result,
        }

    await save_pending_operation(
        user_id=state["user_id"],
        session_id=state["session_id"],
        operation="create",
        entity_type=entity_type,
        arguments=arguments,
        status="creation_failed",
    )

    return {
        "response_type": "chat",
        "response": (
            "<p>I could not create the item.</p>"
        ),
        "data": {
            "type": (
                f"{entity_type}_creation_error"
            ),
            "error": result.get(
                "error"
            ),
            "status_code": result.get(
                "status_code"
            ),
        },
        "result": result,
    }


def build_creation_graph():

    workflow = StateGraph(
        CreationState
    )

    workflow.add_node(
        "load_pending",
        load_pending_node,
    )

    workflow.add_node(
        "merge_data",
        merge_data_node,
    )

    workflow.add_node(
        "validate",
        validate_node,
    )

    workflow.add_node(
        "form",
        build_form_node,
    )

    workflow.add_node(
        "confirmation",
        build_confirmation_node,
    )

    workflow.add_node(
        "create",
        create_node,
    )

    workflow.add_edge(
        START,
        "load_pending",
    )

    workflow.add_edge(
        "load_pending",
        "merge_data",
    )

    workflow.add_edge(
        "merge_data",
        "validate",
    )

    workflow.add_conditional_edges(
        "validate",
        route_after_validation,
        {
            "form": "form",
            "confirmation": "confirmation",
            "create": "create",
        },
    )

    workflow.add_edge(
        "form",
        END,
    )

    workflow.add_edge(
        "confirmation",
        END,
    )

    workflow.add_edge(
        "create",
        END,
    )

    return workflow.compile()


creation_graph = build_creation_graph()


async def run_creation_graph(
    access_token: str,
    current_user: Any,
    user_id: Any,
    session_id: int,
    entity_type: str,
    action: str | None = None,
    arguments: dict[str, Any] | None = None,
    form_data: dict[str, Any] | None = None,
) -> dict[str, Any]:

    entity_type = (
        entity_type.lower().strip()
    )

    if entity_type not in CREATION_CONFIG:

        return {
            "response_type": "chat",
            "response": (
                f"<p>Creation of "
                f"{entity_type} is not "
                "supported yet.</p>"
            ),
            "data": {},
        }

    result = await creation_graph.ainvoke(
        {
            "access_token": access_token,
            "current_user": current_user,
            "user_id": user_id,
            "session_id": session_id,
            "entity_type": entity_type,
            "action": action,
            "arguments": arguments or {},
            "form_data": form_data or {},
        }
    )

    return {
        "response_type": result.get(
            "response_type",
            "chat",
        ),
        "response": result.get(
            "response",
            "",
        ),
        "data": result.get(
            "data",
            {},
        ),
        "arguments": result.get(
            "arguments",
            {},
        ),
        "missing_fields": result.get(
            "missing_fields",
            [],
        ),
        "result": result.get(
            "result"
        ),
    }