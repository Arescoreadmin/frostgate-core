"""Pydantic models shared across the Frostgate backend."""

from pydantic import BaseModel


class Mission(BaseModel):
    """A high-level description of an upcoming Frostgate mission."""

    id: str
    name: str
    status: str
    summary: str
