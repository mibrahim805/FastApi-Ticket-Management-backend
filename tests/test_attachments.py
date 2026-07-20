import asyncio
from io import BytesIO

from bson import ObjectId
from starlette.datastructures import Headers, UploadFile

from uploads import ticket_attachment


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
