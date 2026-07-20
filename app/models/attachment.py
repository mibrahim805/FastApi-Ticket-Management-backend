from datetime import datetime

from pydantic import BaseModel


class Attachment(BaseModel):
    ticket_id: str
    original_name: str
    stored_name: str
    extension: str
    content_type: str
    size: int
    uploaded_by: str
    uploaded_at: datetime
    storage_backend: str = "local"
    storage_key: str | None = None
    checksum_sha256: str | None = None
