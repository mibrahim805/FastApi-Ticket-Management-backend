"""Application entry point for running the Ticket Management API.

Keeping this module as a small wrapper allows both ``uvicorn main:app`` and
the application package to use the same configured FastAPI instance.
"""

from app.main import app

__all__ = ["app"]
