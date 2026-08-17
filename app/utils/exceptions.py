from fastapi import Request, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from fastapi.exceptions import RequestValidationError
from logging import getLogger
from datetime import datetime

logger = getLogger("app.exceptions")

from sqlalchemy.exc import IntegrityError
import re

def add_exception_handlers(app: FastAPI):
    @app.exception_handler(SQLAlchemyError)
    def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError):
        logger.exception("Database error on %s %s", request.method, request.url.path)

        if isinstance(exc, IntegrityError):
            msg = str(exc.orig)
            if "Duplicate entry" in msg:
                match = re.search(r"Duplicate entry '(.+?)' for key", msg)
                if match:
                    dup_val = match.group(1)
                    return JSONResponse(
                        status_code=400,
                        content={"detail": f"The value '{dup_val}' already exists and must be unique."}
                    )
            return JSONResponse(
                status_code=400,
                content={"detail": "A record with this unique value already exists."}
            )

        return JSONResponse(
            status_code=500,
            content={"detail": "Internal Database Error"}
        )

    @app.exception_handler(RequestValidationError)
    def validation_exception_handler(request: Request, exc: RequestValidationError):
        logger.warning("Validation error on %s %s: %s", request.method, request.url.path, exc.errors())
        return JSONResponse(
            status_code=422,
            content={"detail": "Validation Error", "errors": exc.errors()}
        )

    @app.exception_handler(Exception)
    def global_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled exception on %s %s: %s", request.method, request.url.path, str(exc))
        
        return JSONResponse(
            status_code=500,
            content={
                "detail": "An unexpected internal error occurred."
            }
        )
