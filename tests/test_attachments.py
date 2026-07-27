import asyncio
import hashlib
from io import BytesIO

import pytest
from bson import ObjectId
from fastapi import HTTPException
from starlette.datastructures import Headers, UploadFile

from app.api import ticket_attachment
from app.api.permissions import Capability
from app.main import app
from app.storage.attachments import (
    AttachmentStorageError,
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


def test_fake_storage_read_raises_on_missing_key():
    """Exercises the StorageObjectNotFound branch in MemoryAttachmentStorage.read."""
    storage = MemoryAttachmentStorage()
    with pytest.raises(StorageObjectNotFound):
        storage.read("non-existent-key")


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


def _allow_upload(monkeypatch, ticket_id, user_id):
    ticket = {
        "_id": ticket_id,
        "ticket_code": "FILE-1",
        "assigned_agent": str(user_id),
    }
    monkeypatch.setattr(
        ticket_attachment,
        "_get_ticket_or_404",
        lambda _ticket_id: (str(ticket_id), ticket),
    )
    monkeypatch.setattr(
        ticket_attachment,
        "require_ticket_participant",
        lambda *_args: {"role": "member"},
    )


def _allow_attachment_access(monkeypatch, attachment, storage):
    monkeypatch.setattr(
        ticket_attachment,
        "_get_attachment_or_404",
        lambda _attachment_id: (attachment["_id"], attachment),
    )
    monkeypatch.setattr(
        ticket_attachment,
        "_get_ticket_or_404",
        lambda _ticket_id: (str(attachment["ticket_id"]), {}),
    )
    monkeypatch.setattr(
        ticket_attachment,
        "require_ticket_participant",
        lambda *_args: {"role": "member"},
    )
    monkeypatch.setattr(
        ticket_attachment,
        "_storage_for_attachment",
        lambda _attachment: storage,
    )


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


def test_project_scheme_can_revoke_attachment_viewing(
    monkeypatch,
    fake_collection,
    project_members,
):
    project_id = ObjectId()
    ticket_id = ObjectId()
    member_id = ObjectId()
    monkeypatch.setattr(
        ticket_attachment,
        "ticket_collection",
        fake_collection.__class__([_ticket(project_id, ticket_id)]),
    )
    monkeypatch.setattr(
        ticket_attachment,
        "attachment_collection",
        fake_collection.__class__(),
    )
    project_members.insert_one(
        {
            "project_id": project_id,
            "user_id": member_id,
            "role": "member",
            "capability_revocations": [Capability.VIEW_ATTACHMENTS.value],
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        ticket_attachment.list_attachments(
            str(ticket_id),
            current_user={"_id": member_id, "role": "agent"},
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "You do not have permission to view attachments"


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


def test_attachment_storage_cache_reuses_backend(monkeypatch, tmp_path):
    storage = MemoryAttachmentStorage()
    factory_calls = []

    def storage_factory(*, local_root):
        factory_calls.append(local_root)
        return storage

    monkeypatch.setenv("ATTACHMENT_S3_PREFIX", "initial")
    monkeypatch.setattr(ticket_attachment, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(ticket_attachment, "_storage_cache", None)
    monkeypatch.setattr(ticket_attachment, "_storage_cache_signature", None)
    monkeypatch.setattr(
        ticket_attachment,
        "attachment_storage_from_environment",
        storage_factory,
    )

    assert ticket_attachment.get_attachment_storage() is storage
    assert ticket_attachment.get_attachment_storage() is storage
    assert factory_calls == [tmp_path]

    monkeypatch.setenv("ATTACHMENT_S3_PREFIX", "changed")
    assert ticket_attachment.get_attachment_storage() is storage
    assert factory_calls == [tmp_path, tmp_path]


def test_configured_storage_converts_storage_error_to_http_503(monkeypatch):
    def fail_to_configure_storage():
        raise AttachmentStorageError("Storage is unavailable")

    monkeypatch.setattr(
        ticket_attachment,
        "get_attachment_storage",
        fail_to_configure_storage,
    )

    with pytest.raises(HTTPException) as exc_info:
        ticket_attachment._configured_storage()

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Storage is unavailable"


@pytest.mark.parametrize(
    ("collection_name", "lookup", "expected_detail"),
    [
        ("ticket_collection", ticket_attachment._get_ticket_or_404, "Ticket not found"),
        (
            "attachment_collection",
            ticket_attachment._get_attachment_or_404,
            "Attachment not found",
        ),
    ],
)
def test_attachment_lookup_helpers_return_404_for_missing_records(
    monkeypatch,
    fake_collection,
    collection_name,
    lookup,
    expected_detail,
):
    monkeypatch.setattr(ticket_attachment, collection_name, fake_collection)

    with pytest.raises(HTTPException) as exc_info:
        lookup(str(ObjectId()))

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == expected_detail


def test_attachment_storage_key_supports_legacy_metadata_and_rejects_missing_key():
    assert (
        ticket_attachment._attachment_storage_key({"stored_name": "legacy.txt"})
        == "legacy.txt"
    )

    with pytest.raises(HTTPException) as exc_info:
        ticket_attachment._attachment_storage_key({})

    assert exc_info.value.status_code == 500
    assert "storage key" in exc_info.value.detail


def test_storage_for_attachment_rejects_backend_mismatch(monkeypatch):
    monkeypatch.setattr(
        ticket_attachment,
        "get_attachment_storage",
        lambda: MemoryAttachmentStorage(name="local"),
    )

    with pytest.raises(HTTPException) as exc_info:
        ticket_attachment._storage_for_attachment({"storage_backend": "s3"})

    assert exc_info.value.status_code == 503
    assert "'s3'" in exc_info.value.detail
    assert "'local'" in exc_info.value.detail


@pytest.mark.parametrize("filename", [None, "README"])
def test_safe_original_name_requires_a_filename_and_extension(filename):
    with pytest.raises(HTTPException) as exc_info:
        ticket_attachment._safe_original_name(filename)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "File must have a name and extension"


def test_store_attachment_converts_storage_error_to_http_503(monkeypatch):
    storage = MemoryAttachmentStorage()

    def fail_to_store(_key, _content, _content_type):
        raise AttachmentStorageError("Object storage write failed")

    monkeypatch.setattr(storage, "store", fail_to_store)

    with pytest.raises(HTTPException) as exc_info:
        ticket_attachment._store_attachment(
            storage,
            "ticket/file.txt",
            b"content",
            "text/plain",
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Object storage write failed"


def test_upload_rejects_unsupported_extension(monkeypatch):
    ticket_id = ObjectId()
    user_id = ObjectId()
    _allow_upload(monkeypatch, ticket_id, user_id)
    upload = UploadFile(filename="malware.exe", file=BytesIO(b"content"))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            ticket_attachment.upload_attachment(
                str(ticket_id),
                upload,
                current_user={"_id": user_id, "role": "agent"},
            )
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Unsupported file type"


def test_upload_rejects_file_over_size_limit(monkeypatch):
    ticket_id = ObjectId()
    user_id = ObjectId()
    _allow_upload(monkeypatch, ticket_id, user_id)
    monkeypatch.setattr(ticket_attachment, "MAX_FILE_SIZE", 3)
    upload = UploadFile(filename="notes.txt", file=BytesIO(b"four"))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            ticket_attachment.upload_attachment(
                str(ticket_id),
                upload,
                current_user={"_id": user_id, "role": "agent"},
            )
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "File size exceeds 10 MB"


@pytest.mark.parametrize("rollback_fails", [False, True])
def test_upload_rolls_back_storage_and_preserves_insert_error(
    monkeypatch,
    rollback_fails,
):
    class FailingAttachmentCollection:
        @staticmethod
        def insert_one(_document):
            raise RuntimeError("Database insert failed")

    ticket_id = ObjectId()
    user_id = ObjectId()
    storage = MemoryAttachmentStorage()
    _allow_upload(monkeypatch, ticket_id, user_id)
    monkeypatch.setattr(
        ticket_attachment,
        "attachment_collection",
        FailingAttachmentCollection(),
    )
    monkeypatch.setattr(
        ticket_attachment,
        "get_attachment_storage",
        lambda: storage,
    )
    if rollback_fails:

        def fail_to_delete(key):
            storage.deleted_keys.append(key)
            raise AttachmentStorageError("Rollback failed")

        monkeypatch.setattr(storage, "delete", fail_to_delete)

    upload = UploadFile(filename="notes.txt", file=BytesIO(b"content"))

    with pytest.raises(RuntimeError, match="Database insert failed"):
        asyncio.run(
            ticket_attachment.upload_attachment(
                str(ticket_id),
                upload,
                current_user={"_id": user_id, "role": "agent"},
            )
        )

    assert len(storage.deleted_keys) == 1
    assert bool(storage.objects) is rollback_fails


@pytest.mark.parametrize(
    ("storage_error", "expected_status"),
    [
        (StorageObjectNotFound("Attachment object was not found"), 404),
        (AttachmentStorageError("Object storage is unavailable"), 503),
    ],
)
def test_download_converts_storage_errors_to_http_errors(
    monkeypatch,
    storage_error,
    expected_status,
):
    ticket_id = ObjectId()
    user_id = ObjectId()
    attachment = _attachment(ticket_id, user_id, b"content")
    storage = MemoryAttachmentStorage()

    def fail_to_read(_key):
        raise storage_error

    monkeypatch.setattr(storage, "read", fail_to_read)
    _allow_attachment_access(monkeypatch, attachment, storage)

    with pytest.raises(HTTPException) as exc_info:
        ticket_attachment.download_attachment(
            str(attachment["_id"]),
            current_user={"_id": user_id, "role": "agent"},
        )

    assert exc_info.value.status_code == expected_status
    assert exc_info.value.detail == str(storage_error)


def test_download_rejects_checksum_mismatch(monkeypatch):
    ticket_id = ObjectId()
    user_id = ObjectId()
    attachment = _attachment(ticket_id, user_id, b"expected content")
    storage = MemoryAttachmentStorage()
    storage.objects[attachment["storage_key"]] = b"tampered content"
    _allow_attachment_access(monkeypatch, attachment, storage)

    with pytest.raises(HTTPException) as exc_info:
        ticket_attachment.download_attachment(
            str(attachment["_id"]),
            current_user={"_id": user_id, "role": "agent"},
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Attachment failed its integrity check"


def test_delete_converts_storage_error_to_http_503(monkeypatch):
    ticket_id = ObjectId()
    user_id = ObjectId()
    attachment = _attachment(ticket_id, user_id, b"content")
    storage = MemoryAttachmentStorage()

    def fail_to_delete(_key):
        raise AttachmentStorageError("Object storage delete failed")

    monkeypatch.setattr(storage, "delete", fail_to_delete)
    _allow_attachment_access(monkeypatch, attachment, storage)

    with pytest.raises(HTTPException) as exc_info:
        ticket_attachment.delete_attachment(
            str(attachment["_id"]),
            current_user={"_id": user_id, "role": "agent"},
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Object storage delete failed"


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
