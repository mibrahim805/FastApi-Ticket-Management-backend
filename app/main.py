from fastapi import FastAPI

from app.api import registration, ticket, comment, board

app = FastAPI()

app.include_router(registration.router)
app.include_router(board.router)
app.include_router(ticket.router)
app.include_router(comment.router)


