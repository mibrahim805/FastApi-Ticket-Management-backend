from fastapi import FastAPI

from app.api import (
    activity,
    board,
    comment,
    notifications,
    registration,
    reports,
    status,
    ticket,
)
from uploads import ticket_attachment

# from uploads import ticket_attachment
app = FastAPI()

app.include_router(registration.router)
app.include_router(board.router)
app.include_router(ticket.router)
app.include_router(comment.router)
app.include_router(status.router)
app.include_router(activity.router)
app.include_router(notifications.router)
app.include_router(reports.router)
app.include_router(ticket_attachment.router)
