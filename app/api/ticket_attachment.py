import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from bson import ObjectId
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from starlette.concurrency import run_in_threadpool

from app.api.activity import log_activity
from app.api.auth import get_current_user
from app.api.permissions import (
    Capability,
    can_delete_attachment,
    can_upload_attachment,
    parse_object_id,
    require_capability,
    require_ticket_participant,
)
from app.db.db import (
    attachment_collection,
    project_members_collection,
    ticket_collection,
)
from app.models.attachment import Attachment
from app.storage.attachments import (
    AttachmentStorage,
    AttachmentStorageError,
    StorageObjectNotFound,
    attachment_storage_from_environment,
)

router = APIRouter(tags=["Attachments"])

UPLOAD_DIR = Path("uploads/tickets")

ALLOWED_EXTENSIONS = {
    "png",
    "jpg",
    "jpeg",
    "pdf",
    "docx",
    "xlsx",
    "txt",
    "zip",
    "py",
}

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

_storage_cache_signature = None
_storage_cache: AttachmentStorage | None = None


def _storage_configuration_signature() -> tuple:
    names = (
        "ATTACHMENT_STORAGE_BACKEND",
        "ATTACHMENT_LOCAL_DIR",
        "ATTACHMENT_S3_BUCKET",
        "ATTACHMENT_S3_PREFIX",
        "ATTACHMENT_S3_ENDPOINT_URL",
        "ATTACHMENT_S3_REGION",
        "ATTACHMENT_S3_ADDRESSING_STYLE",
        "ATTACHMENT_S3_SSE",
    )
    return (str(UPLOAD_DIR.resolve()), *(os.getenv(name) for name in names))


def get_attachment_storage() -> AttachmentStorage:
    global _storage_cache, _storage_cache_signature

    signature = _storage_configuration_signature()
    if _storage_cache is None or signature != _storage_cache_signature:
        _storage_cache = attachment_storage_from_environment(local_root=UPLOAD_DIR)
        _storage_cache_signature = signature
    return _storage_cache


def _configured_storage() -> AttachmentStorage:
    try:
        return get_attachment_storage()
    except AttachmentStorageError as error:
        raise HTTPException(status_code=503, detail=str(error))


def _get_ticket_or_404(ticket_id: str) -> tuple[str, dict]:
    ticket_object_id = parse_object_id(ticket_id, "ticket_id")
    ticket = ticket_collection.find_one({"_id": ticket_object_id})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return str(ticket_object_id), ticket


def _get_attachment_or_404(attachment_id: str) -> tuple[ObjectId, dict]:
    attachment_object_id = parse_object_id(attachment_id, "attachment_id")
    attachment = attachment_collection.find_one({"_id": attachment_object_id})
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")
    return attachment_object_id, attachment


def _require_attachment_upload_permission(
    ticket: dict,
    current_user: dict,
    membership: dict,
) -> None:
    if not can_upload_attachment(ticket, current_user, membership):
        raise HTTPException(
            status_code=403,
            detail=(
                "Only the ticket creator, assigned agent, project lead, or "
                "a role granted upload permission can upload attachments"
            ),
        )


def _attachment_storage_key(attachment: dict) -> str:
    storage_key = attachment.get("storage_key") or attachment.get("stored_name")
    if not storage_key:
        raise HTTPException(
            status_code=500,
            detail="Attachment metadata is missing its storage key",
        )
    return str(storage_key)


def _storage_for_attachment(attachment: dict) -> AttachmentStorage:
    storage = _configured_storage()
    recorded_backend = attachment.get("storage_backend", "local")
    if recorded_backend != storage.name:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Attachment uses the '{recorded_backend}' backend but "
                f"'{storage.name}' is currently configured"
            ),
        )
    return storage


def _serialize_attachment(attachment: dict) -> dict:
    serialized = dict(attachment)
    attachment_id = str(serialized.pop("_id"))
    serialized["attachment_id"] = attachment_id
    serialized["download_url"] = f"/attachments/{attachment_id}"
    serialized.pop("storage_key", None)
    serialized.pop("checksum_sha256", None)
    return serialized


def _safe_original_name(filename: str | None) -> str:
    original_name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not original_name or "." not in original_name:
        raise HTTPException(
            status_code=400,
            detail="File must have a name and extension",
        )
    return original_name


def _stored_name(ticket: dict, extension: str) -> str:
    ticket_code = re.sub(
        r"[^A-Za-z0-9_-]+",
        "-",
        str(ticket.get("ticket_code") or "ticket"),
    ).strip("-")
    return f"{ticket_code}-{uuid4().hex}.{extension}"


def _store_attachment(
    storage: AttachmentStorage,
    key: str,
    content: bytes,
    content_type: str,
) -> None:
    try:
        storage.store(key, content, content_type)
    except AttachmentStorageError as error:
        raise HTTPException(status_code=503, detail=str(error))


@router.post(
    "/tickets/{ticket_id}/attachments",
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    ticket_id: str,
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
):
    canonical_ticket_id, ticket = _get_ticket_or_404(ticket_id)
    membership = require_ticket_participant(
        ticket,
        current_user,
        project_members_collection,
    )
    _require_attachment_upload_permission(ticket, current_user, membership)

    original_name = _safe_original_name(file.filename)
    extension = original_name.rsplit(".", 1)[-1].lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported file type")

    content = await file.read(MAX_FILE_SIZE + 1)
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File size exceeds 10 MB")

    storage = _configured_storage()
    stored_name = _stored_name(ticket, extension)
    storage_key = f"{canonical_ticket_id}/{stored_name}"
    content_type = file.content_type or "application/octet-stream"
    await run_in_threadpool(
        _store_attachment,
        storage,
        storage_key,
        content,
        content_type,
    )

    attachment = Attachment(
        ticket_id=canonical_ticket_id,
        original_name=original_name,
        stored_name=stored_name,
        extension=extension,
        content_type=content_type,
        size=len(content),
        uploaded_by=str(current_user["_id"]),
        uploaded_at=datetime.now(timezone.utc),
        storage_backend=storage.name,
        storage_key=storage_key,
        checksum_sha256=hashlib.sha256(content).hexdigest(),
    )

    try:
        result = attachment_collection.insert_one(attachment.model_dump())
    except Exception:
        try:
            await run_in_threadpool(storage.delete, storage_key)
        except AttachmentStorageError:
            pass
        raise

    log_activity(
        ticket_id=canonical_ticket_id,
        action="attachment_uploaded",
        changed_by=current_user["_id"],
        changes=[
            {
                "field": "attachment",
                "old_value": None,
                "new_value": {
                    "attachment_id": str(result.inserted_id),
                    "file_name": stored_name,
                },
            }
        ],
    )

    return {
        "message": "Attachment uploaded successfully",
        "attachment_id": str(result.inserted_id),
        "file_name": stored_name,
    }


@router.get("/tickets/{ticket_id}/attachments")
def list_attachments(
    ticket_id: str,
    current_user=Depends(get_current_user),
):
    canonical_ticket_id, ticket = _get_ticket_or_404(ticket_id)
    membership = require_ticket_participant(
        ticket,
        current_user,
        project_members_collection,
    )
    require_capability(
        membership,
        Capability.VIEW_ATTACHMENTS,
        detail="You do not have permission to view attachments",
    )
    attachments = attachment_collection.find({"ticket_id": canonical_ticket_id}).sort(
        "uploaded_at", 1
    )
    return [_serialize_attachment(attachment) for attachment in attachments]


@router.get("/attachments/{attachment_id}")
def download_attachment(
    attachment_id: str,
    current_user=Depends(get_current_user),
):
    _, attachment = _get_attachment_or_404(attachment_id)
    _, ticket = _get_ticket_or_404(str(attachment.get("ticket_id")))
    membership = require_ticket_participant(
        ticket,
        current_user,
        project_members_collection,
    )
    require_capability(
        membership,
        Capability.VIEW_ATTACHMENTS,
        detail="You do not have permission to view attachments",
    )

    storage = _storage_for_attachment(attachment)
    try:
        content = storage.read(_attachment_storage_key(attachment))
    except StorageObjectNotFound as error:
        raise HTTPException(status_code=404, detail=str(error))
    except AttachmentStorageError as error:
        raise HTTPException(status_code=503, detail=str(error))

    checksum = attachment.get("checksum_sha256")
    if checksum and hashlib.sha256(content).hexdigest() != checksum:
        raise HTTPException(
            status_code=500,
            detail="Attachment failed its integrity check",
        )

    original_name = str(attachment.get("original_name") or "attachment")
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(original_name)}",
        "X-Content-Type-Options": "nosniff",
    }
    return Response(
        content=content,
        media_type=attachment.get("content_type") or "application/octet-stream",
        headers=headers,
    )


@router.delete("/attachments/{attachment_id}")
def delete_attachment(
    attachment_id: str,
    current_user=Depends(get_current_user),
):
    attachment_object_id, attachment = _get_attachment_or_404(attachment_id)
    ticket_id, ticket = _get_ticket_or_404(str(attachment.get("ticket_id")))
    membership = require_ticket_participant(
        ticket,
        current_user,
        project_members_collection,
    )

    if not can_delete_attachment(attachment, current_user, membership):
        raise HTTPException(
            status_code=403,
            detail=(
                "Only the uploader, project lead, or a role granted delete "
                "permission can delete this attachment"
            ),
        )

    storage = _storage_for_attachment(attachment)
    try:
        storage.delete(_attachment_storage_key(attachment))
    except AttachmentStorageError as error:
        raise HTTPException(status_code=503, detail=str(error))

    attachment_collection.delete_one({"_id": attachment_object_id})
    log_activity(
        ticket_id=ticket_id,
        action="attachment_deleted",
        changed_by=current_user["_id"],
        changes=[
            {
                "field": "attachment",
                "old_value": {
                    "attachment_id": str(attachment_object_id),
                    "file_name": attachment.get("stored_name"),
                },
                "new_value": None,
            }
        ],
    )
    return {"message": "Attachment deleted successfully"}
