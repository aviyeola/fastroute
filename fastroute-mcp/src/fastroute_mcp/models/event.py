from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Optional
from pydantic import BaseModel, Field


class NormalizedEvent(BaseModel):
    schema_version: int = 1
    event_id: int
    job_id: str
    type: str
    source: str = "fastroute_sse"
    occurred_at: Optional[str] = None
    received_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    payload: dict[str, Any] = Field(default_factory=dict)
