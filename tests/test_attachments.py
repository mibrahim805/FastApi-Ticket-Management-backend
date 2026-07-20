import asyncio
import hashlib
from io import BytesIO

import pytest
from bson import ObjectId
from fastapi import HTTPException
from starlette.datastructures import Headers, UploadFile

from app.api import ticket_attachment
from app.main import app
from app.storage.attachments import (
    S3AttachmentStorage,
    StorageObjectNotFound,
    attachment_storage_from_environment,
)


class MemoryAttachmentStorage:
    def __init__(self, name="local"):
        self.name = name
        self.objects = {}
        self.content_types = {}
        self.deleted_keys = []

    def store(self, key, content, content_type):
        self.objects[key] = content
        self.content_types[key] = content_type

    def read(self, key):
        if key not in self.objects:
            raise StorageObjectNotFound("Attachment object was not found")
        return self.objects[key]

    def delete(self, key):
        self.deleted_keys.append(key)
        self.objects.pop(key, None)


def _ticket(project_id, ticket_id, **overrides):
    return {
        "_id": ticket_id,
        "project_id": str(project_id),
        "ticket_code": "FILE-1",
        "userId": str(ObjectId()),
        "assigned_agent": None,
        **overrides,
    }


def _attachment(ticket_id, uploader_id, content, **overrides):
    attachment_id = overrides.pop("_id", ObjectId())
    storage_key = overrides.pop(
        "storage_key",
        f"{ticket_id}/stored.txt",
    )
    return {
        "_id": attachment_id,
        "ticket_id": str(ticket_id),
        "original_name": "notes.txt",
        "stored_name": "stored.txt",
        "extension": "txt",
        "content_type": "text/plain",
        "size": len(content),
        "uploaded_by": str(uploader_id),
        "uploaded_at": "2026-07-20T10:00:00Z",
        "storage_backend": "local",
        "storage_key": storage_key,
        "checksum_sha256": hashlib.sha256(content).hexdigest(),
        **overrides,
    }


def test_attachment_upload_writes_ticket_activity(
    monkeypatch,
    tmp_path,
    fake_collection,
    ticket_activities,
    grant_project_role,
):
    project_id = ObjectId()
    ticket_id = ObjectId()
    user_id = ObjectId()
    tickets = fake_collection.__class__(
        [
            {
                "_id": ticket_id,
                "project_id": str(project_id),
                "ticket_code": "FILE-1",
                "assigned_agent": str(user_id),
            }
        ]
    )
    attachments = fake_collection.__class__()
    monkeypatch.setattr(ticket_attachment, "ticket_collection", tickets)
    monkeypatch.setattr(ticket_attachment, "attachment_collection", attachments)
    monkeypatch.setattr(ticket_attachment, "UPLOAD_DIR", tmp_path)
    grant_project_role(project_id, user_id, "member")
    upload = UploadFile(
        filename="notes.txt",
        file=BytesIO(b"workflow notes"),
        headers=Headers({"content-type": "text/plain"}),
    )

    response = asyncio.run(
        ticket_attachment.upload_attachment(
            str(ticket_id),
            upload,
            current_user={"_id": user_id, "role": "agent"},
        )
    )

    assert response["message"] == "Attachment uploaded successfully"
    assert ticket_activities.documents[-1]["action"] == "attachment_uploaded"
    assert ticket_activities.documents[-1]["changes"][0]["new_value"][
        "attachment_id"
    ] == str(attachments.documents[0]["_id"])


def test_upload_rejects_user_without_ticket_access(
    monkeypatch,
    fake_collection,
):
    project_id = ObjectId()
    ticket_id = ObjectId()
    user_id = ObjectId()
    attachments = fake_collection.__class__()
    storage = MemoryAttachmentStorage()
    monkeypatch.setattr(
        ticket_attachment,
        "ticket_collection",
        fake_collection.__class__([_ticket(project_id, ticket_id)]),
    )
    monkeypatch.setattr(ticket_attachment, "attachment_collection", attachments)
    monkeypatch.setattr(
        ticket_attachment,
        "get_attachment_storage",
        lambda: storage,
    )
    upload = UploadFile(
        filename="notes.txt",
        file=BytesIO(b"private notes"),
        headers=Headers({"content-type": "text/plain"}),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            ticket_attachment.upload_attachment(
                str(ticket_id),
                upload,
                current_user={"_id": user_id, "role": "agent"},
            )
        )

    assert exc_info.value.status_code == 403
    assert attachments.documents == []
    assert storage.objects == {}


def test_upload_records_storage_metadata_and_bytes(
    monkeypatch,
    fake_collection,
    grant_project_role,
):
    project_id = ObjectId()
    ticket_id = ObjectId()
    user_id = ObjectId()
    attachments = fake_collection.__class__()
    storage = MemoryAttachmentStorage(name="s3")
    monkeypatch.setattr(
        ticket_attachment,
        "ticket_collection",
        fake_collection.__class__(
            [_ticket(project_id, ticket_id, assigned_agent=str(user_id))]
        ),
    )
    monkeypatch.setattr(ticket_attachment, "attachment_collection", attachments)
    monkeypatch.setattr(
        ticket_attachment,
        "get_attachment_storage",
        lambda: storage,
    )
    grant_project_role(project_id, user_id, "member")
    content = b"stored in object storage"
    upload = UploadFile(
        filename="notes.txt",
        file=BytesIO(content),
        headers=Headers({"content-type": "text/plain"}),
    )

    asyncio.run(
        ticket_attachment.upload_attachment(
            str(ticket_id),
            upload,
            current_user={"_id": user_id, "role": "agent"},
        )
    )

    metadata = attachments.documents[0]
    assert metadata["storage_backend"] == "s3"
    assert metadata["storage_key"].startswith(f"{ticket_id}/FILE-1-")
    assert metadata["checksum_sha256"] == hashlib.sha256(content).hexdigest()
    assert storage.objects[metadata["storage_key"]] == content


def test_unassigned_project_member_cannot_upload_attachment(
    monkeypatch,
    fake_collection,
    grant_project_role,
):
    project_id = ObjectId()
    ticket_id = ObjectId()
    member_id = ObjectId()
    attachments = fake_collection.__class__()
    storage = MemoryAttachmentStorage()
    monkeypatch.setattr(
        ticket_attachment,
        "ticket_collection",
        fake_collection.__class__([_ticket(project_id, ticket_id)]),
    )
    monkeypatch.setattr(ticket_attachment, "attachment_collection", attachments)
    monkeypatch.setattr(
        ticket_attachment,
        "get_attachment_storage",
        lambda: storage,
    )
    grant_project_role(project_id, member_id, "member")
    upload = UploadFile(
        filename="notes.txt",
        file=BytesIO(b"not allowed"),
        headers=Headers({"content-type": "text/plain"}),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            ticket_attachment.upload_attachment(
                str(ticket_id),
                upload,
                current_user={"_id": member_id, "role": "agent"},
            )
        )

    assert exc_info.value.status_code == 403
    assert "assigned agent" in exc_info.value.detail
    assert attachments.documents == []
    assert storage.objects == {}


def test_list_attachments_requires_access_and_serializes_download_url(
    monkeypatch,
    fake_collection,
    grant_project_role,
):
    project_id = ObjectId()
    ticket_id = ObjectId()
    member_id = ObjectId()
    outsider_id = ObjectId()
    content = b"notes"
    attachment = _attachment(ticket_id, member_id, content)
    monkeypatch.setattr(
        ticket_attachment,
        "ticket_collection",
        fake_collection.__class__([_ticket(project_id, ticket_id)]),
    )
    monkeypatch.setattr(
        ticket_attachment,
        "attachment_collection",
        fake_collection.__class__([attachment]),
    )
    grant_project_role(project_id, member_id, "member")

    response = ticket_attachment.list_attachments(
        str(ticket_id),
        current_user={"_id": member_id, "role": "agent"},
    )

    assert response[0]["attachment_id"] == str(attachment["_id"])
    assert response[0]["download_url"] == f"/attachments/{attachment['_id']}"
    assert "storage_key" not in response[0]

    with pytest.raises(HTTPException) as exc_info:
        ticket_attachment.list_attachments(
            str(ticket_id),
            current_user={"_id": outsider_id, "role": "agent"},
        )
    assert exc_info.value.status_code == 403


def test_download_attachment_returns_bytes_for_ticket_member(
    monkeypatch,
    fake_collection,
    grant_project_role,
):
    project_id = ObjectId()
    ticket_id = ObjectId()
    member_id = ObjectId()
    content = b"download me"
    attachment = _attachment(ticket_id, member_id, content)
    storage = MemoryAttachmentStorage()
    storage.objects[attachment["storage_key"]] = content
    monkeypatch.setattr(
        ticket_attachment,
        "ticket_collection",
        fake_collection.__class__([_ticket(project_id, ticket_id)]),
    )
    monkeypatch.setattr(
        ticket_attachment,
        "attachment_collection",
        fake_collection.__class__([attachment]),
    )
    monkeypatch.setattr(
        ticket_attachment,
        "get_attachment_storage",
        lambda: storage,
    )
    grant_project_role(project_id, member_id, "member")

    response = ticket_attachment.download_attachment(
        str(attachment["_id"]),
        current_user={"_id": member_id, "role": "agent"},
    )

    assert response.body == content
    assert response.media_type == "text/plain"
    assert "notes.txt" in response.headers["content-disposition"]


def test_delete_attachment_rejects_non_owner_member(
    monkeypatch,
    fake_collection,
    grant_project_role,
):
    project_id = ObjectId()
    ticket_id = ObjectId()
    uploader_id = ObjectId()
    other_member_id = ObjectId()
    content = b"keep me"
    attachment = _attachment(ticket_id, uploader_id, content)
    attachments = fake_collection.__class__([attachment])
    storage = MemoryAttachmentStorage()
    storage.objects[attachment["storage_key"]] = content
    monkeypatch.setattr(
        ticket_attachment,
        "ticket_collection",
        fake_collection.__class__([_ticket(project_id, ticket_id)]),
    )
    monkeypatch.setattr(ticket_attachment, "attachment_collection", attachments)
    monkeypatch.setattr(
        ticket_attachment,
        "get_attachment_storage",
        lambda: storage,
    )
    grant_project_role(project_id, other_member_id, "member")

    with pytest.raises(HTTPException) as exc_info:
        ticket_attachment.delete_attachment(
            str(attachment["_id"]),
            current_user={"_id": other_member_id, "role": "agent"},
        )

    assert exc_info.value.status_code == 403
    assert attachments.count_documents({}) == 1
    assert storage.objects[attachment["storage_key"]] == content


def test_uploader_can_delete_attachment_and_storage_object(
    monkeypatch,
    fake_collection,
    ticket_activities,
    grant_project_role,
):
    project_id = ObjectId()
    ticket_id = ObjectId()
    uploader_id = ObjectId()
    content = b"delete me"
    attachment = _attachment(ticket_id, uploader_id, content)
    attachments = fake_collection.__class__([attachment])
    storage = MemoryAttachmentStorage()
    storage.objects[attachment["storage_key"]] = content
    monkeypatch.setattr(
        ticket_attachment,
        "ticket_collection",
        fake_collection.__class__([_ticket(project_id, ticket_id)]),
    )
    monkeypatch.setattr(ticket_attachment, "attachment_collection", attachments)
    monkeypatch.setattr(
        ticket_attachment,
        "get_attachment_storage",
        lambda: storage,
    )
    grant_project_role(project_id, uploader_id, "member")

    response = ticket_attachment.delete_attachment(
        str(attachment["_id"]),
        current_user={"_id": uploader_id, "role": "agent"},
    )

    assert response == {"message": "Attachment deleted successfully"}
    assert attachments.documents == []
    assert attachment["storage_key"] not in storage.objects
    assert ticket_activities.documents[-1]["action"] == "attachment_deleted"


def test_s3_storage_uses_compatible_client_api():
    class FakeS3Client:
        def __init__(self):
            self.objects = {}
            self.put_calls = []

        def put_object(self, **options):
            self.put_calls.append(options)
            self.objects[(options["Bucket"], options["Key"])] = options["Body"]

        def get_object(self, *, Bucket, Key):
            return {"Body": BytesIO(self.objects[(Bucket, Key)])}

        def delete_object(self, *, Bucket, Key):
            self.objects.pop((Bucket, Key), None)

    client = FakeS3Client()
    storage = S3AttachmentStorage(
        bucket="ticket-files",
        prefix="production",
        endpoint_url="https://objects.example.test",
        server_side_encryption="AES256",
        client=client,
    )

    storage.store("ticket-id/file.txt", b"content", "text/plain")

    assert storage.read("ticket-id/file.txt") == b"content"
    assert client.put_calls[0]["Key"] == "production/ticket-id/file.txt"
    assert client.put_calls[0]["ContentType"] == "text/plain"
    assert client.put_calls[0]["ServerSideEncryption"] == "AES256"

    storage.delete("ticket-id/file.txt")
    assert client.objects == {}


def test_environment_selects_s3_compatible_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("ATTACHMENT_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("ATTACHMENT_S3_BUCKET", "ticket-files")
    monkeypatch.setenv("ATTACHMENT_S3_PREFIX", "production")
    monkeypatch.setenv(
        "ATTACHMENT_S3_ENDPOINT_URL",
        "https://objects.example.test",
    )
    monkeypatch.setenv("ATTACHMENT_S3_ADDRESSING_STYLE", "path")

    storage = attachment_storage_from_environment(local_root=tmp_path)

    assert isinstance(storage, S3AttachmentStorage)
    assert storage.bucket == "ticket-files"
    assert storage.prefix == "production"
    assert storage.endpoint_url == "https://objects.example.test"
    assert storage.addressing_style == "path"


def test_attachment_routes_are_exposed_in_openapi():
    paths = app.openapi()["paths"]

    assert {"post", "get"} <= set(paths["/tickets/{ticket_id}/attachments"])
    assert {"get", "delete"} <= set(paths["/attachments/{attachment_id}"])
