from pydantic import BaseModel




class Board(BaseModel):
    title: str
    code : str
    ticket_counter: int = 0
    created_by: str
    total_tickets: int
    class Config:
        orm_mode = True


class BoardCreate(BaseModel):
    title: str
    code: str


class AssignBoard(BaseModel):
    agent_id: str




