from fastapi import FastAPI

from app.api import board, comment, registration, ticket

app = FastAPI()

app.include_router(registration.router)
app.include_router(board.router)
app.include_router(ticket.router)
app.include_router(comment.router)
