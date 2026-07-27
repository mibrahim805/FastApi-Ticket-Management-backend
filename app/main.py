from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import (
    activity,
    comment,
    filters,
    notifications,
    project,
    registration,
    reports,
    sprint,
    status,
    ticket,
    ticket_attachment,
)
from app.db.db import (
    ensure_database_indexes,
    migrate_legacy_global_roles,
    migrate_legacy_workflow_transitions,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_database_indexes()
    migrate_legacy_global_roles()
    migrate_legacy_workflow_transitions()
    yield


# from uploads import ticket_attachment
app = FastAPI(lifespan=lifespan)

app.include_router(registration.router)
app.include_router(project.router)
app.include_router(ticket.router)
app.include_router(comment.router)
app.include_router(filters.router)
app.include_router(status.router)
app.include_router(activity.router)
app.include_router(notifications.router)
app.include_router(reports.router)
app.include_router(sprint.router)
app.include_router(ticket_attachment.router)
