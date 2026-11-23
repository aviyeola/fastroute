from __future__ import annotations
from typing import Any, Optional
from pydantic import BaseModel, Field


class UnroutedConnection(BaseModel):
    net: Optional[str] = None
    item1: Optional[dict[str, Any]] = None
    item2: Optional[dict[str, Any]] = None
    start: Optional[dict[str, Any]] = None
    end: Optional[dict[str, Any]] = None
    diagnosis: Optional[str] = None
    diagnosis_details: Optional[dict[str, Any]] = None
    raw: dict[str, Any] = Field(default_factory=dict)


class ClearanceViolation(BaseModel):
    layer: Optional[str] = None
    location: Optional[dict[str, Any]] = None
    required_mm: Optional[float] = None
    actual_mm: Optional[float] = None
    unfixable: bool = False
    items: list[Any] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class NormalizedReport(BaseModel):
    schema_version: int = 1
    parser_version: str = "0.1.0"
    raw_path: Optional[str] = None
    fastroute_version: Optional[str] = None
    input_path: Optional[str] = None
    timings: dict[str, Any] = Field(default_factory=dict)
    board_stats: dict[str, Any] = Field(default_factory=dict)
    router_score: Optional[float] = None
    optimizer_score: Optional[float] = None
    unrouted_count: int = 0
    ignored_unrouted_count: int = 0
    violations_count: int = 0
    unfixable_violations_count: int = 0
    vias_count: Optional[int] = None
    blind_buried_vias_count: Optional[int] = None
    traces_count: Optional[int] = None
    trace_length_mm: Optional[float] = None
    unrouted_connections: list[UnroutedConnection] = Field(default_factory=list)
    clearance_violations: list[ClearanceViolation] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)
