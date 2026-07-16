from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import (
    activity,
    comment,
    notifications,
    project,
    registration,
    reports,
    status,
    ticket,
)
from app.db.db import ensure_database_indexes
from uploads import ticket_attachment


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_database_indexes()
    yield


# from uploads import ticket_attachment
app = FastAPI(lifespan=lifespan)

app.include_router(registration.router)
app.include_router(project.router)
app.include_router(ticket.router)
app.include_router(comment.router)
app.include_router(status.router)
app.include_router(activity.router)
app.include_router(notifications.router)
app.include_router(reports.router)
app.include_router(ticket_attachment.router)
