from fastapi import FastAPI

from app.api import activity, board, comment, registration, status, ticket

# from uploads import ticket_attachment
app = FastAPI()

app.include_router(registration.router)
app.include_router(board.router)
app.include_router(ticket.router)
app.include_router(comment.router)
app.include_router(status.router)
app.include_router(activity.router)
