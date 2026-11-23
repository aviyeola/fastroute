from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class JobState(str, Enum):
    created = "created"
    queued = "queued"
    preparing = "preparing"
    running = "running"
    stop_requested = "stop_requested"
    finalizing = "finalizing"
    completed = "completed"
    completed_partial = "completed_partial"
    stopped = "stopped"
    failed = "failed"
    cancelled_before_start = "cancelled_before_start"
    interrupted = "interrupted"


class Job(BaseModel):
    job_id: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    state: JobState = JobState.created
    board_path: str
    board_sha256: Optional[str] = None
    dsn_path: Optional[str] = None
    ses_path: Optional[str] = None
    report_path: Optional[str] = None
    routed_board_path: Optional[str] = None
    drc_report_path: Optional[str] = None
    config_hash: Optional[str] = None
    fastroute_version: Optional[str] = None
    fastroute_binary: Optional[str] = None
    kicad_version: Optional[str] = None
    parent_job_id: Optional[str] = None
    attempt_index: int = 1
    profile: str = "balanced"
    effective_config: dict = Field(default_factory=dict)
    argv: list[str] = Field(default_factory=list)
    pid: Optional[int] = None
    live_port: Optional[int] = None
    exit_code: Optional[int] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    stop_requested_at: Optional[str] = None
    workspace: str
    initial_session_path: Optional[str] = None
    initial_session_hash: Optional[str] = None
