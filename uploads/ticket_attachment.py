from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.api.auth import get_current_user
from app.api.permissions import parse_object_id, require_ticket_membership
from app.db.db import (
    attachment_collection,
    board_members_collection,
    ticket_collection,
)
from app.models.attachment import Attachment

router = APIRouter(prefix="/tickets", tags=["Attachments"])

UPLOAD_DIR = Path("uploads/tickets")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

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


@router.post("/{ticket_id}/attachments", status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    ticket_id: str,
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
):

    ticket = ticket_collection.find_one(
        {"_id": parse_object_id(ticket_id, "ticket_id")}
    )

    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    require_ticket_membership(
        ticket,
        current_user,
        board_members_collection,
    )

    extension = file.filename.split(".")[-1].lower()

    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported file type")

    content = await file.read()

    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File size exceeds 10 MB")

    attachment_count = attachment_collection.count_documents({"ticket_id": ticket_id})

    stored_name = f"{ticket['ticket_code']}-{attachment_count + 1}.{extension}"

    file_path = UPLOAD_DIR / stored_name

    with open(file_path, "wb") as buffer:
        buffer.write(content)

    attachment = Attachment(
        ticket_id=ticket_id,
        original_name=file.filename,
        stored_name=stored_name,
        extension=extension,
        content_type=file.content_type,
        size=len(content),
        uploaded_by=str(current_user["_id"]),
        uploaded_at=datetime.now(timezone.utc),
    )

    result = attachment_collection.insert_one(attachment.model_dump())

    return {
        "message": "Attachment uploaded successfully",
        "attachment_id": str(result.inserted_id),
        "file_name": stored_name,
    }
