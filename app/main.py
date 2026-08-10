from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

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
from app.api.ticket_attachment import get_attachment_storage
from app.db.db import client as mongo_client
from app.db.db import (
    ensure_database_indexes,
    migrate_legacy_global_roles,
    migrate_legacy_workflow_transitions,
    migrate_status_metadata,
)
from app.storage.attachments import LocalAttachmentStorage


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_database_indexes()
    migrate_legacy_global_roles()
    migrate_status_metadata()
    migrate_legacy_workflow_transitions()
    yield


# from uploads import ticket_attachment
app = FastAPI(lifespan=lifespan)


@app.get("/health/live", tags=["health"])
async def health_live() -> dict[str, str]:
    """Report that the API process is running, without checking dependencies."""
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
async def health_ready() -> dict:
    checks = {}
    try:
        mongo_client.admin.command("ping")
        checks["mongodb"] = "ok"
    except Exception:
        checks["mongodb"] = "unavailable"
    try:
        storage = get_attachment_storage()
        if isinstance(storage, LocalAttachmentStorage):
            if not storage.root.is_dir():
                raise OSError("attachment directory is unavailable")
        else:
            storage._get_client().head_bucket(Bucket=storage.bucket)
        checks["attachments"] = "ok"
    except Exception:
        checks["attachments"] = "unavailable"
    if "unavailable" in checks.values():
        raise HTTPException(
            status_code=503, detail={"status": "unavailable", "checks": checks}
        )
    return {"status": "ok", "checks": checks}


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
