from typing import Literal

from pydantic import BaseModel

from app.database.session import DatabaseStatus


class HealthResponse(BaseModel):
    status: Literal["healthy", "unhealthy"]
    application: str
    database: DatabaseStatus
