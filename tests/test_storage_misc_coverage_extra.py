import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType

import pytest
from bson import ObjectId
from fastapi import HTTPException
from pydantic import ValidationError

from app.db import db as database
from app.decorator.super_admin import super_admin_required
from app.models.activity import TicketActivity
from app.models.project import ProjectCreate, ProjectUpdate
from app.models.ticket import BulkAssignRequest, BulkTransitionRequest
from app.models.transition import TransitionCreate, TransitionGuard
from app.storage import attachments as storage_module
from app.storage.attachments import (
    LocalAttachmentStorage,
    S3AttachmentStorage,
    StorageConfigurationError,
    StorageObjectNotFound,
    StorageOperationError,
    attachment_storage_from_environment,
)


def test_local_storage_round_trip_and_key_validation(tmp_path):
    root = tmp_path / "attachments"
    storage = LocalAttachmentStorage(root)

    storage.store("ticket/file.txt", b"contents", "text/plain")

    assert storage.read("ticket/file.txt") == b"contents"
    storage.delete("ticket/file.txt")
    storage.delete("ticket/file.txt")
    assert not (root / "ticket" / "file.txt").exists()

    for key in ("", ".", "/absolute.txt", "../outside.txt"):
        with pytest.raises(StorageConfigurationError):
            storage_module._validated_key(key)


def test_local_storage_rejects_a_resolved_path_outside_its_root(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    storage = LocalAttachmentStorage(root)

    with pytest.raises(
        StorageConfigurationError,
        match="escapes storage root",
    ):
        storage._path("escape/file.txt")


def test_local_storage_converts_root_creation_failure(monkeypatch, tmp_path):
    def fail_to_create_directory(*_args, **_kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(Path, "mkdir", fail_to_create_directory)

    with pytest.raises(
        StorageConfigurationError,
        match="Could not create the local attachment directory",
    ):
        LocalAttachmentStorage(tmp_path / "unavailable")


def test_local_store_failure_before_temporary_file_creation(monkeypatch, tmp_path):
    storage = LocalAttachmentStorage(tmp_path)

    def fail_to_create_temporary_file(*_args, **_kwargs):
        raise OSError("temporary file unavailable")

    monkeypatch.setattr(
        storage_module.tempfile,
        "mkstemp",
        fail_to_create_temporary_file,
    )

    with pytest.raises(StorageOperationError, match="Could not store attachment"):
        storage.store("ticket/file.txt", b"contents", "text/plain")


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_local_store_cleans_up_after_a_write_failure(
    monkeypatch,
    tmp_path,
    cleanup_fails,
):
    storage = LocalAttachmentStorage(tmp_path)
    temporary_path = tmp_path / "temporary-file"

    monkeypatch.setattr(
        storage_module.tempfile,
        "mkstemp",
        lambda **_kwargs: (123, str(temporary_path)),
    )

    def fail_to_open(*_args, **_kwargs):
        raise OSError("write failed")

    monkeypatch.setattr(storage_module.os, "fdopen", fail_to_open)

    if cleanup_fails:

        def fail_to_unlink(*_args, **_kwargs):
            raise OSError("cleanup failed")

        monkeypatch.setattr(Path, "unlink", fail_to_unlink)

    with pytest.raises(StorageOperationError, match="Could not store attachment"):
        storage.store("ticket/file.txt", b"contents", "text/plain")


def test_local_read_and_delete_convert_operating_system_errors(
    monkeypatch,
    tmp_path,
):
    storage = LocalAttachmentStorage(tmp_path)

    with pytest.raises(StorageObjectNotFound, match="was not found"):
        storage.read("missing.txt")

    class BrokenPath:
        @staticmethod
        def read_bytes():
            raise PermissionError("cannot read")

        @staticmethod
        def unlink(*_args, **_kwargs):
            raise PermissionError("cannot delete")

    monkeypatch.setattr(storage, "_path", lambda _key: BrokenPath())

    with pytest.raises(StorageOperationError, match="Could not read attachment"):
        storage.read("blocked.txt")
    with pytest.raises(StorageOperationError, match="Could not delete attachment"):
        storage.delete("blocked.txt")


def test_s3_configuration_and_object_key_validation():
    with pytest.raises(StorageConfigurationError, match="S3_BUCKET"):
        S3AttachmentStorage(bucket="")
    with pytest.raises(StorageConfigurationError, match="ADDRESSING_STYLE"):
        S3AttachmentStorage(bucket="files", addressing_style="invalid")

    prefixed = S3AttachmentStorage(
        bucket="files",
        prefix="/production/",
        client=object(),
    )
    unprefixed = S3AttachmentStorage(bucket="files", client=object())

    assert prefixed._object_key("ticket/file.txt") == "production/ticket/file.txt"
    assert unprefixed._object_key("ticket/file.txt") == "ticket/file.txt"
    assert prefixed._get_client() is prefixed._client


def test_s3_client_creation_passes_optional_configuration(monkeypatch):
    calls = []

    class FakeConfig:
        def __init__(self, **options):
            self.options = options

    boto3_module = ModuleType("boto3")

    def create_client(service, **options):
        client = object()
        calls.append((service, options, client))
        return client

    boto3_module.client = create_client
    botocore_module = ModuleType("botocore")
    botocore_module.__path__ = []
    botocore_config_module = ModuleType("botocore.config")
    botocore_config_module.Config = FakeConfig
    monkeypatch.setitem(sys.modules, "boto3", boto3_module)
    monkeypatch.setitem(sys.modules, "botocore", botocore_module)
    monkeypatch.setitem(sys.modules, "botocore.config", botocore_config_module)

    configured = S3AttachmentStorage(
        bucket="files",
        endpoint_url="https://objects.example.test",
        region_name="test-1",
        addressing_style="path",
    )
    first_client = configured._get_client()
    assert configured._get_client() is first_client

    minimal = S3AttachmentStorage(bucket="files")
    minimal._get_client()

    assert calls[0][0] == "s3"
    assert calls[0][1]["endpoint_url"] == "https://objects.example.test"
    assert calls[0][1]["region_name"] == "test-1"
    assert calls[0][1]["config"].options == {"s3": {"addressing_style": "path"}}
    assert set(calls[1][1]) == {"config"}


def test_s3_client_creation_requires_boto3(monkeypatch):
    monkeypatch.setitem(sys.modules, "boto3", None)

    with pytest.raises(StorageConfigurationError, match="boto3 is required"):
        S3AttachmentStorage(bucket="files")._get_client()


class _S3Body:
    def __init__(self, content, *, has_close=True):
        self.content = content
        self.closed = False
        if not has_close:
            self.close = None

    def read(self):
        return self.content

    def close(self):
        self.closed = True


class _RecordingS3Client:
    def __init__(self, body):
        self.body = body
        self.put_calls = []
        self.get_calls = []
        self.delete_calls = []

    def put_object(self, **options):
        self.put_calls.append(options)

    def get_object(self, **options):
        self.get_calls.append(options)
        return {"Body": self.body}

    def delete_object(self, **options):
        self.delete_calls.append(options)


def test_s3_operations_cover_optional_headers_and_body_cleanup():
    closable_body = _S3Body(b"first")
    client = _RecordingS3Client(closable_body)
    storage = S3AttachmentStorage(
        bucket="files",
        prefix="production",
        server_side_encryption="AES256",
        client=client,
    )

    storage.store("ticket/file.txt", b"first", "text/plain")
    assert storage.read("ticket/file.txt") == b"first"
    storage.delete("ticket/file.txt")

    assert client.put_calls == [
        {
            "Bucket": "files",
            "Key": "production/ticket/file.txt",
            "Body": b"first",
            "ContentType": "text/plain",
            "ServerSideEncryption": "AES256",
        }
    ]
    assert closable_body.closed is True

    body_without_close = _S3Body(b"second", has_close=False)
    client_without_options = _RecordingS3Client(body_without_close)
    storage_without_options = S3AttachmentStorage(
        bucket="files",
        client=client_without_options,
    )
    storage_without_options.store("plain.txt", b"second", "text/plain")
    assert storage_without_options.read("plain.txt") == b"second"
    assert "ServerSideEncryption" not in client_without_options.put_calls[0]


class _S3ServiceError(RuntimeError):
    def __init__(self, response=None):
        super().__init__("S3 failed")
        self.response = response


@pytest.mark.parametrize(
    ("operation", "expected_message"),
    [
        ("store", "Could not store attachment in S3"),
        ("read", "Could not read attachment from S3"),
        ("delete", "Could not delete attachment from S3"),
    ],
)
def test_s3_operations_convert_unexpected_errors(
    operation,
    expected_message,
):
    class FailingClient:
        def __getattr__(self, _name):
            def fail(**_kwargs):
                raise _S3ServiceError()

            return fail

    storage = S3AttachmentStorage(bucket="files", client=FailingClient())

    with pytest.raises(StorageOperationError, match=expected_message):
        if operation == "store":
            storage.store("file.txt", b"content", "text/plain")
        elif operation == "read":
            storage.read("file.txt")
        else:
            storage.delete("file.txt")


@pytest.mark.parametrize("operation", ["store", "read", "delete"])
def test_s3_operations_preserve_storage_errors(operation):
    class FailingClient:
        def __getattr__(self, _name):
            def fail(**_kwargs):
                raise StorageConfigurationError("configuration failed")

            return fail

    storage = S3AttachmentStorage(bucket="files", client=FailingClient())

    with pytest.raises(StorageConfigurationError, match="configuration failed"):
        if operation == "store":
            storage.store("file.txt", b"content", "text/plain")
        elif operation == "read":
            storage.read("file.txt")
        else:
            storage.delete("file.txt")


@pytest.mark.parametrize(
    "response",
    [
        {"Error": {"Code": "NoSuchKey"}},
        {"ResponseMetadata": {"HTTPStatusCode": 404}},
    ],
)
def test_s3_read_converts_not_found_responses(response):
    class MissingClient:
        @staticmethod
        def get_object(**_kwargs):
            raise _S3ServiceError(response)

    storage = S3AttachmentStorage(bucket="files", client=MissingClient())

    with pytest.raises(StorageObjectNotFound, match="was not found"):
        storage.read("missing.txt")

    assert storage._is_not_found(_S3ServiceError(None)) is False


def test_storage_factory_selects_local_s3_and_rejects_unknown_backend(
    monkeypatch,
    tmp_path,
):
    configured_root = tmp_path / "configured"
    monkeypatch.setenv("ATTACHMENT_STORAGE_BACKEND", " LOCAL ")
    monkeypatch.setenv("ATTACHMENT_LOCAL_DIR", str(configured_root))
    local = attachment_storage_from_environment(local_root=tmp_path / "fallback")
    assert isinstance(local, LocalAttachmentStorage)
    assert local.root == configured_root.resolve()

    monkeypatch.delenv("ATTACHMENT_LOCAL_DIR")
    local_fallback = attachment_storage_from_environment(
        local_root=tmp_path / "fallback",
    )
    assert local_fallback.root == (tmp_path / "fallback").resolve()

    monkeypatch.setenv("ATTACHMENT_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("ATTACHMENT_S3_BUCKET", "files")
    monkeypatch.setenv("ATTACHMENT_S3_PREFIX", "production")
    monkeypatch.setenv("ATTACHMENT_S3_ENDPOINT_URL", "https://objects.test")
    monkeypatch.setenv("ATTACHMENT_S3_REGION", "test-1")
    monkeypatch.setenv("ATTACHMENT_S3_ADDRESSING_STYLE", "virtual")
    monkeypatch.setenv("ATTACHMENT_S3_SSE", "AES256")
    s3 = attachment_storage_from_environment(local_root=tmp_path)
    assert isinstance(s3, S3AttachmentStorage)
    assert s3.server_side_encryption == "AES256"

    monkeypatch.setenv("ATTACHMENT_STORAGE_BACKEND", "unsupported")
    with pytest.raises(StorageConfigurationError, match="either local or s3"):
        attachment_storage_from_environment(local_root=tmp_path)


def test_database_index_creation_calls_every_collection(monkeypatch):
    class RecordingCollection:
        def __init__(self):
            self.calls = []

        def create_index(self, keys, **options):
            self.calls.append((keys, options))

    collections = {}
    for name in (
        "user_collection",
        "projects_collection",
        "project_members_collection",
        "project_components_collection",
        "statuses_collection",
        "issue_links_collection",
        "transitions_collection",
        "notification_preferences_collection",
        "saved_filters_collection",
        "rate_limits_collection",
        "sprints_collection",
        "ticket_collection",
    ):
        collection = RecordingCollection()
        collections[name] = collection
        monkeypatch.setattr(database, name, collection)

    database.ensure_database_indexes()

    assert len(collections["projects_collection"].calls) == 1
    assert len(collections["project_members_collection"].calls) == 1
    assert len(collections["project_components_collection"].calls) == 1
    assert len(collections["statuses_collection"].calls) == 2
    assert len(collections["issue_links_collection"].calls) == 3
    assert len(collections["transitions_collection"].calls) == 2
    assert len(collections["notification_preferences_collection"].calls) == 1
    assert len(collections["saved_filters_collection"].calls) == 1
    assert len(collections["rate_limits_collection"].calls) == 1
    assert len(collections["sprints_collection"].calls) == 2
    assert len(collections["ticket_collection"].calls) == 2


def test_legacy_workflow_migration_covers_skips_defaults_and_updates(
    monkeypatch,
    fake_collection,
):
    untouched_id = ObjectId()
    empty_id = ObjectId()
    existing_id = ObjectId()
    migrated_id = ObjectId()
    projects = fake_collection.__class__(
        [
            {"_id": untouched_id},
            {"_id": empty_id, "workflow_transitions": None},
            {
                "_id": existing_id,
                "workflow_transitions": [
                    {
                        "name": "Already migrated",
                        "from_status_ids": ["open"],
                        "to_status_id": "done",
                    }
                ],
            },
            {
                "_id": migrated_id,
                "created_by": "creator",
                "workflow_transitions": [
                    {
                        "name": "Complete",
                        "from_status_ids": ["open", "open"],
                        "to_status_id": 42,
                    },
                    {
                        "name": "Legacy",
                        "from_status_id": "new",
                        "to_status_id": "open",
                        "guards": {
                            "allowed_roles": ["lead"],
                            "requires_assignee": True,
                        },
                    },
                    {
                        "from_status_ids": ["open"],
                        "to_status_id": "done",
                    },
                    {"name": "No source", "to_status_id": "done"},
                    {"name": "No target", "from_status_ids": ["open"]},
                ],
            },
        ]
    )
    transitions = fake_collection.__class__(
        [{"project_id": existing_id, "name": "Already migrated"}]
    )
    monkeypatch.setattr(database, "projects_collection", projects)
    monkeypatch.setattr(database, "transitions_collection", transitions)

    assert database.migrate_legacy_workflow_transitions() == 2

    migrated = [
        transition
        for transition in transitions.documents
        if transition["project_id"] == migrated_id
    ]
    assert migrated[0]["from_status_ids"] == ["open"]
    assert migrated[0]["to_status_id"] == "42"
    assert migrated[0]["guard"] == {
        "allowed_roles": ["lead", "member"],
        "requires_assignee": False,
    }
    assert migrated[1]["guard"] == {
        "allowed_roles": ["lead"],
        "requires_assignee": True,
    }
    assert "workflow_transitions" not in projects.find_one({"_id": empty_id})
    assert "workflow_transitions" not in projects.find_one({"_id": existing_id})
    assert "workflow_transitions" not in projects.find_one({"_id": migrated_id})


def test_super_admin_decorator_supports_sync_and_async_functions():
    @super_admin_required
    def sync_action(*, current_user=None):
        return "sync result"

    @super_admin_required
    async def async_action(*, current_user=None):
        return "async result"

    for action in (sync_action, async_action):
        invoke = (
            (lambda: asyncio.run(action()))
            if asyncio.iscoroutinefunction(action)
            else action
        )
        with pytest.raises(HTTPException) as unauthenticated:
            invoke()
        assert unauthenticated.value.status_code == 401

        invoke_as_member = (
            (lambda: asyncio.run(action(current_user={"role": "member"})))
            if asyncio.iscoroutinefunction(action)
            else (lambda: action(current_user={"role": "member"}))
        )
        with pytest.raises(HTTPException) as forbidden:
            invoke_as_member()
        assert forbidden.value.status_code == 403

    assert sync_action(current_user={"role": "super_admin"}) == "sync result"
    assert (
        asyncio.run(async_action(current_user={"role": "super_admin"}))
        == "async result"
    )


def test_model_validators_cover_success_and_failure_paths():
    now = datetime.now(timezone.utc)
    activity = TicketActivity(
        ticket_id="ticket",
        action="updated",
        changed_by="user",
        changed_at=now,
    )
    assert activity.changed_at == now

    assert ProjectCreate(name="  Project  ", code="PROJ").name == "Project"
    assert ProjectUpdate(name=None).name is None
    assert ProjectUpdate(name="  Renamed  ").name == "Renamed"
    with pytest.raises(ValidationError, match="Project name cannot be blank"):
        ProjectCreate(name="   ", code="PROJ")
    with pytest.raises(ValidationError, match="Project name cannot be blank"):
        ProjectUpdate(name="   ")

    assert BulkTransitionRequest(
        ticket_ids=["one", "two"],
        transition_id="transition",
    ).ticket_ids == ["one", "two"]
    assert BulkAssignRequest(
        ticket_ids=["one", "two"],
        agent_id="agent",
    ).ticket_ids == ["one", "two"]
    with pytest.raises(ValidationError, match="ticket_ids must be unique"):
        BulkTransitionRequest(
            ticket_ids=["one", "one"],
            transition_id="transition",
        )
    with pytest.raises(ValidationError, match="ticket_ids must be unique"):
        BulkAssignRequest(ticket_ids=["one", "one"], agent_id="agent")

    assert TransitionGuard().requires_assignee is False
    assert (
        TransitionCreate(
            name="  Complete  ",
            from_status_ids=["open", "reopened"],
            to_status_id="done",
        ).name
        == "Complete"
    )
    with pytest.raises(ValidationError, match="Transition name cannot be blank"):
        TransitionCreate(
            name="   ",
            from_status_ids=["open"],
            to_status_id="done",
        )
    with pytest.raises(ValidationError, match="from_status_ids must be unique"):
        TransitionCreate(
            name="Complete",
            from_status_ids=["open", "open"],
            to_status_id="done",
        )
