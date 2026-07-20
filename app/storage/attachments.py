import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path, PurePosixPath


class AttachmentStorageError(Exception):
    """Base exception for attachment storage failures."""


class StorageConfigurationError(AttachmentStorageError):
    """Raised when the selected backend is not configured correctly."""


class StorageObjectNotFound(AttachmentStorageError):
    """Raised when attachment bytes do not exist in storage."""


class StorageOperationError(AttachmentStorageError):
    """Raised when a configured backend cannot complete an operation."""


def _validated_key(key: str) -> PurePosixPath:
    object_key = PurePosixPath(key)
    if (
        not key
        or object_key.is_absolute()
        or ".." in object_key.parts
        or object_key.as_posix() in {"", "."}
    ):
        raise StorageConfigurationError("Invalid attachment storage key")
    return object_key


class AttachmentStorage(ABC):
    name: str

    @abstractmethod
    def store(self, key: str, content: bytes, content_type: str) -> None:
        """Persist attachment bytes under a logical object key."""

    @abstractmethod
    def read(self, key: str) -> bytes:
        """Return all bytes for a stored attachment."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Delete attachment bytes. Missing objects are treated as deleted."""


class LocalAttachmentStorage(AttachmentStorage):
    name = "local"

    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise StorageConfigurationError(
                "Could not create the local attachment directory"
            ) from error

    def _path(self, key: str) -> Path:
        object_key = _validated_key(key)
        candidate = self.root.joinpath(*object_key.parts).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise StorageConfigurationError("Attachment path escapes storage root")
        return candidate

    def store(self, key: str, content: bytes, content_type: str) -> None:
        destination = self._path(key)
        temporary_name = None
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".attachment-",
                dir=destination.parent,
            )
            with os.fdopen(descriptor, "wb") as temporary_file:
                temporary_file.write(content)
            os.replace(temporary_name, destination)
        except OSError as error:
            if temporary_name is not None:
                try:
                    Path(temporary_name).unlink(missing_ok=True)
                except OSError:
                    pass
            raise StorageOperationError("Could not store attachment") from error

    def read(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError as error:
            raise StorageObjectNotFound("Attachment object was not found") from error
        except OSError as error:
            raise StorageOperationError("Could not read attachment") from error

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink(missing_ok=True)
        except OSError as error:
            raise StorageOperationError("Could not delete attachment") from error


class S3AttachmentStorage(AttachmentStorage):
    name = "s3"

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "",
        endpoint_url: str | None = None,
        region_name: str | None = None,
        addressing_style: str = "auto",
        server_side_encryption: str | None = None,
        client=None,
    ):
        if not bucket:
            raise StorageConfigurationError(
                "ATTACHMENT_S3_BUCKET is required for S3 storage"
            )
        if addressing_style not in {"auto", "path", "virtual"}:
            raise StorageConfigurationError(
                "ATTACHMENT_S3_ADDRESSING_STYLE must be auto, path, or virtual"
            )

        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.endpoint_url = endpoint_url
        self.region_name = region_name
        self.addressing_style = addressing_style
        self.server_side_encryption = server_side_encryption
        self._client = client

    def _object_key(self, key: str) -> str:
        logical_key = _validated_key(key).as_posix()
        if self.prefix:
            return f"{self.prefix}/{logical_key}"
        return logical_key

    def _get_client(self):
        if self._client is not None:
            return self._client

        try:
            import boto3
            from botocore.config import Config
        except ImportError as error:
            raise StorageConfigurationError(
                "boto3 is required when ATTACHMENT_STORAGE_BACKEND=s3"
            ) from error

        options = {
            "config": Config(
                s3={"addressing_style": self.addressing_style},
            )
        }
        if self.endpoint_url:
            options["endpoint_url"] = self.endpoint_url
        if self.region_name:
            options["region_name"] = self.region_name
        self._client = boto3.client("s3", **options)
        return self._client

    @staticmethod
    def _is_not_found(error: Exception) -> bool:
        response = getattr(error, "response", {}) or {}
        code = str(response.get("Error", {}).get("Code", ""))
        status_code = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        return code in {"NoSuchKey", "NotFound", "404"} or status_code == 404

    def store(self, key: str, content: bytes, content_type: str) -> None:
        options = {
            "Bucket": self.bucket,
            "Key": self._object_key(key),
            "Body": content,
            "ContentType": content_type,
        }
        if self.server_side_encryption:
            options["ServerSideEncryption"] = self.server_side_encryption
        try:
            self._get_client().put_object(**options)
        except AttachmentStorageError:
            raise
        except Exception as error:
            raise StorageOperationError("Could not store attachment in S3") from error

    def read(self, key: str) -> bytes:
        try:
            response = self._get_client().get_object(
                Bucket=self.bucket,
                Key=self._object_key(key),
            )
            body = response["Body"]
            try:
                return body.read()
            finally:
                close = getattr(body, "close", None)
                if close:
                    close()
        except AttachmentStorageError:
            raise
        except Exception as error:
            if self._is_not_found(error):
                raise StorageObjectNotFound(
                    "Attachment object was not found"
                ) from error
            raise StorageOperationError("Could not read attachment from S3") from error

    def delete(self, key: str) -> None:
        try:
            self._get_client().delete_object(
                Bucket=self.bucket,
                Key=self._object_key(key),
            )
        except AttachmentStorageError:
            raise
        except Exception as error:
            raise StorageOperationError(
                "Could not delete attachment from S3"
            ) from error


def attachment_storage_from_environment(
    *,
    local_root: Path | str,
) -> AttachmentStorage:
    backend = os.getenv("ATTACHMENT_STORAGE_BACKEND", "local").strip().casefold()
    if backend == "local":
        configured_root = os.getenv("ATTACHMENT_LOCAL_DIR")
        return LocalAttachmentStorage(configured_root or local_root)
    if backend == "s3":
        return S3AttachmentStorage(
            bucket=os.getenv("ATTACHMENT_S3_BUCKET", ""),
            prefix=os.getenv("ATTACHMENT_S3_PREFIX", ""),
            endpoint_url=os.getenv("ATTACHMENT_S3_ENDPOINT_URL"),
            region_name=os.getenv("ATTACHMENT_S3_REGION"),
            addressing_style=os.getenv(
                "ATTACHMENT_S3_ADDRESSING_STYLE",
                "auto",
            ),
            server_side_encryption=os.getenv("ATTACHMENT_S3_SSE"),
        )
    raise StorageConfigurationError(
        "ATTACHMENT_STORAGE_BACKEND must be either local or s3"
    )
